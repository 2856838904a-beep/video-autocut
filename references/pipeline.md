# 完整流程与命令

所有脚本位于本 Skill 的 `scripts/` 目录。下文的 `$S` 指该目录,`$W` 指工作目录。

依赖:ffmpeg/ffprobe(含 `hevc_cuvid`/`h264_nvenc`)、Python 3.11+、`faster-whisper`、`numpy`。
N 卡硬件编解码是可选加速(实测 2.6x 解 / 6~9x 编)。

---

## ① 探测素材

```bash
python $S/analyze_tracks.py --src "G:\录像\MC"     # 标准第一步:逐文件列出音轨
```

输出每个文件的时长、分辨率、fps、**音轨数量**、码率。
**先看音轨数**:OBS 分轨录制会有 4 条。只有 1 条的素材可能没录到解说。

> 单看时长/分辨率也可以直接 `ffprobe -v error -show_streams <文件>`,
> `analyze_tracks.py` 的价值在于把**多音轨**一次列全并给活跃度初判。
> (早期笔记里的单文件探测脚本已从本仓库移除 —— 别照抄老笔记。)

---

## ② 抽解说轨(自动挑人声轨)

```bash
python $S/extract_audio.py --src "G:\录像\MC" --dst work/_audio --jobs 1 --track auto
```

- 逐轨采样测活跃度(>-35dB 时间占比),挑最活跃的
- 输出 `<name>.wav`(16kHz 单声道)+ `<name>.track.json`(记录选了哪条、各轨活跃度)
- `--track N` 可强制指定绝对 stream 索引
- 全库 44 小时实测 493 秒,产出约 5 GB

**验证**:某文件转写出 0 句 = 轨挑错了。用 `per_track.py` 复核各轨活跃度。

---

## ③ GPU 分块转写

```bash
python $S/transcribe_long.py work/_audio/xxx.wav --out work/xxx.json \
       --chunk-min 20 --model small --device cuda
```

- 默认 20 分钟一块,内存恒定;每块落盘,中断可续跑(`<out>.chunks/` 目录)
- 实测 **50x 实时**(RTX 5070 Ti,small + float16)
- 首块会慢(模型加载 + cuBLAS autotune),之后恒定
- `--device cpu` 自动降级(会打印警告)

输出 JSON:`meta`(时长/句数/语言/倍速)+ `segments[]`,每段含 `start/end/text/avg_logprob/no_speech_prob/words[]`(词级时间戳)。

**判读要点**:
- `no_speech_prob > 0.6` 的句子可能是幻觉,做统计时注意
- 语言自动识别,中文会输出 `zh`(可能混简体/繁体,属正常)

---

## ④ 画面分析

> **算力选择(2026-11 影之刃 45.8 小时实战实测,本机 32 核 + RTX 5070 Ti):**
>
> | 并行方式 | 聚合吞吐 | 45.8h 预计 |
> |---|---|---|
> | 软解 1 路 | 50x | 55 分钟 |
> | 软解 4 路(纯 CPU) | 21x | 107 分钟(越并越慢) |
> | 硬解 4 路(纯 NVDEC) | 32x | 86 分钟 |
> | **软 3 + 硬 3(混合)** | **43x** | **≈47 分钟** |
>
> 反直觉但实测两次:**单路硬解比软解慢**(这批是低码率 1080p60,cuvi d 只有 19x,
> 软解 50x),可是 GPU 解码**并行扩展性更好**;把 CPU 解码和 NVDEC 掺在一起,
> 两条通路互不抢资源,总吞吐最高。**别开 `--hw` 单跑,也别纯软解堆到 4 路以上。**
>
> 另外试过「只解关键帧」(`-skip_frame nokey`)能到 170x,但与全量分析的
> 运动 Top10% 只有 35~48% 重合 —— **会漏掉近一半战斗高光,不能用**。



```bash
python $S/analyze.py "G:\录像\MC\xxx.mkv" --audio work/_audio/xxx.wav \
       --fps 1 --hw --out work/xxx.metrics.json
```

- `--audio` 复用已抽的 wav,避免为响度再解一次 1080p60
- `--hw` 用 `hevc_cuvid`/`h264_cuvid` 硬解(实测 2.61x)
- `--fps 1` 对游戏录像够用;vlog 空镜建议 2

输出逐秒窗口:`loud`(dBFS)、`motion`(0~100 逐帧差分)、`cut`(镜头切换)、`silent`、`dark`。

**性能**:104 帧/秒,6 小时素材约 38 分钟(软解)/ 15 分钟(硬解)。

**注意**:MC 这类游戏没有镜头切换,`cut` 几乎恒为 0 —— 别用切换点找高光,要用 **motion 突变**。

---

## ⑤ 完整对话事件选段

**统一入口,品类靠 `--genre` 切换**(加品类改 `scripts/genres.py`,不要新建脚本):

```bash
# 列出所有品类及各自的时长默认值
python $S/pick.py --list

# 生存类 / 沙盒类(已用真实 MC 录像验证)
python $S/pick.py --transcript work/xxx.json --metrics work/xxx.metrics.json \
       --genre survival --top 8 --out work/xxx.cuts.json

# 射击竞技 / vlog 空镜 / 口播讲解(词典为推导,首次请小样抽检)
python $S/pick.py --transcript work/xxx.json --metrics work/xxx.metrics.json --genre shooter
python $S/pick.py --transcript work/xxx.json --metrics work/xxx.metrics.json --genre vlog
python $S/pick.py --transcript work/xxx.json --metrics work/xxx.metrics.json --genre talk
```

品类默认值(可被命令行覆盖):

| 品类 | gap | split-gap | min~max | budget | motion_div | 空镜 |
|---|---|---|---|---|---|---|
| survival | auto p93 | 3.0 | 15~120s | 0.35 | 25 | — |
| shooter | auto p93 | 2.5 | 8~45s | 0.30 | 12 | — |
| vlog | auto p93 | 4.0 | 8~60s | 0.40 | 40 | ★ 是 |
| talk | auto p93 | 3.0 | 20~180s | 0.60 | 60 | — |

命令行参数:

| 参数 | 说明 |
|---|---|
| `--gap` | 事件切分的停顿阈值。留空 = 从停顿分布 p93 自动推 |
| `--split-gap` | 长事件内部按此停顿细分(只在呼吸口切,不切句子) |
| `--pad` | 每段首尾呼吸余量,默认 0.5s |
| `--min` / `--max` | 时长上下限 |
| `--budget-ratio` | 成片总长占素材比例 |
| `--tier` | 成片形态:`short` / `medium` / `long` / `auto` / `none` |
| `--max-unit` | 单个片段时长上限(超过就逐级降停顿阈值继续切) |
| `--budget-sec` | 成片总长硬上限(秒),优先于比例 |
| `--top` | 段数上限 |

**成片形态(短视频 / 中视频 / 长视频)用 `--tier` 切。** 同一批素材,做 60 秒切片
和做 20 分钟长片的选段参数是**相反**的,不必自己手调,档位在 `tiers.py` 里定义一次:

| 档位 | 成片目标 | 预算 | 段长 | 单段上限 | 最多段数 |
|---|---|---|---|---|---|
| `short` | 短视频切片(单条 ≤60s) | 60s(≤素材 25%) | 4~20s | 10s | 4 |
| `medium` | 中视频(3~8 分钟) | 300s(≤素材 40%) | 6~40s | 18s | 10 |
| `long` | 长视频(15~40 分钟) | 1200s(≤素材 55%) | 10~120s | 40s | 40 |
| `auto` | 按素材总长自动选:<3min→short / <30min→medium / 否则 long | — | — | — | — |

```bash
python $S/pick.py --transcript work/xxx.json --metrics work/xxx.metrics.json \
       --genre survival --tier short --out work/xxx.short.cuts.json
python $S/pick.py --transcript work/xxx.json --metrics work/xxx.metrics.json \
       --genre survival --tier auto          # 不想拍脑袋就用 auto
python $S/pick.py --list-tiers                # 打印全部分档
```

**优先级:显式参数 > `--tier` > 品类默认。** `--max-unit 33`、`--budget-sec 600`
写死在命令行上的值永远压过档位(`scripts/selftest.py` 有断言覆盖这条,
别把优先级改反了)。

输出报告含**三个部分**:选段(带逐句文本 + 价值维度明细)、空镜候选(vlog 品类)、**未入选的完整事件**(供人核对,避免漏掉好内容)。

**判读要点**:
- 「未入选事件」里有高价值分(如 `价值 11.0`)的,说明被 budget 挤掉了 —— 调大 `--budget-ratio` 或 `--top`
- 维度全是"危机冲突"的段落偏吵闹;维度多样的段落通常更有内容
- `静音%` 很高(>50%)说明这段其实有不少空档,可能该缩短

---

## ⑥ 压紧节奏(只压空隙,不切语音)

选段为保对话完整会留大量停顿 —— **实测静音占比 49~83%**。`tighten.py` 只压
超阈值的句间空隙,不切任何语音;档位同样复用 `tiers.py`:

```bash
python $S/tighten.py --cuts work/xxx.cuts.json --transcript work/xxx.json \
       --metrics work/xxx.metrics.json --tier medium --out work/xxx.tight.cuts.json
```

| 档位 | `--max-gap` | 效果 |
|---|---|---|
| `short` | 0.5 | 句句接上,短视频节奏 |
| `medium` | 1.0 | 保留自然呼吸(默认推荐) |
| `long` | 2.0 | 只削冗长沉默,接近原样 |

**判据是「片段内残留的最长静音」,不是总长。** short 档把空隙切得更碎,
`min_seg` 又更小,总长可能反而略多于 medium —— 但残留静音一定更短。
`scripts/selftest.py` 就是按这个不变量断言的。

`--mode hold` **不推荐**:固定留边无法与句长对齐,实测 19/19 段全部切在句子中间。
详见 `references/gotchas.md`。

---

## ⑦ 导出片段(画面 + 解说轨)

```bash
python $S/cut_clips.py --cuts work/xxx.cuts.json --src "G:\录像\MC\xxx.mkv" \
       --track 3 --dst work/_clips
```

- **`--track` 是绝对 stream 索引**(不是 `a:N` 的 N),脚本自动换算
- 必须指定解说轨,否则导出的是游戏声
- 用 `h264_nvenc` 硬编,实测 6~9x 实时
- 输出 `<base>_cutNN_HHMM-HHMM.mp4` + `_clips.json` 清单

**验证**:对导出片段测音频活跃度,应明显 >20%(只有游戏声的话活跃度会低很多)。

---

## ⑧ 生成剪映草稿

```bash
python $S/make_draft.py --name "MC高光_0708" \
       --out-root work/_stage --final-root "$LOCALAPPDATA/JianyingPro/User Data/Projects/com.lveditor.draft" \
       --media-src <素材原始位置> \
       --clip a.mp4 --clip "b.mp4|1.5|4.0"     # 相对路径用 | 分隔起止秒
```

- `--final-root`:草稿最终位置。**先在暂存区生成再整体搬走时必用** —— 它把所有路径一次写对,避免事后做字符串替换(替换会踩"前缀污染"的坑)
- `--media-src`:素材的原始位置(单个文件)。避免素材从暂存目录拷贝后路径失效
- 从本机已有明文草稿自动读 `version`/`new_version`,从 `template.tmp` 读骨架,不硬编码
- 拒绝覆盖同名草稿,除非设 `AUTOCUT_ALLOW_REPLACE=1`

---

## ⑨ 校验

```bash
python $S/validate_draft.py <草稿目录>
```

校验:骨架字段覆盖、**三条 ID 链**、时间线无缝、`duration` 一致、素材可达、悬空引用。

三种模式自动识别:
- **明文 meta** → 完整校验
- **meta 已被剪映加密** → 只读校验(跳过链 2)
- **预写 final-root 但还在暂存区** → 素材可达性降级为提示

---

## ⑩ 装入草稿库

```bash
# 剪映已关闭:直接装
python $S/install_testdraft.py work/_stage/MC高光_0708

# 剪映还开着:等它退出后自动装
python $S/install_when_closed.py work/_stage/MC高光_0708 --timeout 2400
```

`install_when_closed.py` 轮询 `tasklist`,检测到主程序退出就安装 —— 免去"关掉再回来跑命令"。
托盘进程(`JianyingProTray.exe`)不构成覆盖风险,可以留着。

---

## ⑪ 清理中间产物

**别一上来就删。** 这条流水线的磁盘产物是成片体积的几十倍(实测两个批次
258 GB:分段视频、暂存副本、分块转写、混合音轨),规则固化在清单里:

```bash
cd $S
python cleanup_work.py --dry         # 列出将删项与可回收体积
python cleanup_work.py --yes         # 真删
python cleanup_work.py --scan        # 新批次:发现工作根并登记
python cleanup_work.py --verify-only # 只做素材完整性校验
```

规则文件:`<技能根>/work_manifest.json`。**改清单,不改代码。**

```jsonc
{
  "roots": [
    {"path": "G:\\autocut_work\\mc",
     "keep":   ["_game", "*.cuts.json", "*.json"],   // 永不删
     "remove": ["_clips", "_clips_sep", "*.log"]}    // 要删(只在第一层匹配)
  ],
  "drafts":      {"keep": ["MC全库精华"], "remove": []},
  "media_check": {"drafts": ["MC全库精华"]}          // 删前校验素材完整
}
```

**为什么必须留这些**:

| 路径 | 为什么 |
|---|---|
| `mc/_game`、`dabiaoge/*.game.wav`、`*.voice.wav` | 游戏原声/解说音轨源,重建草稿要用 |
| `_audio_final` | 轨判定结果 + 双轨音频 |
| 各批次 `*.cuts.json` / `*.json` | 选段与文稿,**唯一产出**,且体积可忽略 |
| `<草稿>/_media/` | 成品自带素材,与工作目录无关,别动 |

**可以放心删**:`_clips*`、`_stage*`、`*.chunks`、`_mix*.m4a`、`masters/*_v2`、
`wheels`。

**删前的安全闸**(`cleanup_work.py` 内置):先遍历成品草稿全部时间线的素材
路径确认存在,缺一个就中止。实测通过 `MC全库精华 914/914`、
`RDR全库精华 473/473`。别用 `--skip-verify` 绕它。

> 旧脚本 `cleanup_intermediate.py` 已弃用并归档到 `scripts/_archive/`(路径写死在
> 已不存在的 `~\.dsh\skills`,且只清草稿不清磁盘产物)。

---

## 全库分批建议

44 小时全量跑约 2.5 小时(转写 53 分 + 分析 90 分)。若要更快拿到全貌:

1. **只跑 ①②③**(约 1 小时)→ 得到全库文稿地图,能看出哪个文件内容最丰富
2. 挑 2~3 个文件跑 ④⑤⑥ → 验证选段与节奏
3. 确认方向后补齐其余文件的 ④,再批量 ⑦⑧⑨⑩

**不要一上来就全库生成草稿** —— 万一品类词典不对,返工代价很大。
