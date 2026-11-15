---
name: video-autocut
description: 从长时素材里自动挑选高光片段并生成可编辑的剪映专业版草稿。适用于六类长素材的自动粗剪:游戏录像(生存/沙盒类如MC、单机剧情向3A如荒野大镖客/GTA、射击竞技类、动作/武侠ARPG如影之刃/黑神话/只狼)、vlog旅行空镜、口播讲解。当用户提到「长素材选段、自动剪辑、高光提取、游戏录像剪辑、剧情向剪辑、故事剪辑、过场动画、剪映草稿、剪映工程、批量粗剪、解说挑选、录播精华、vlog空镜筛选、去废片」时使用。覆盖:OBS多音轨识别与游戏原声/解说分轨(输出双音轨草稿,音量各自可调)、GPU分块转写、画面运动分析、过场黑边检测、完整对话事件切分、按品类词典打分、按短视频/中视频/长视频分档选段、长素材连续剧分集(13分钟一集)、导出片段、程序化生成剪映草稿。
disable-model-invocation: false
---

# 长素材自动剪辑(大类)

把几十小时素材变成「已挑好、已排好序的剪映草稿」。

**五个品类**:

| 品类 | 状态 | 核心信号 | 组装脚本 |
|---|---|---|---|
| `survival` 生存/沙盒(MC、泰拉瑞亚) | ✅ 已验证 | 完整对话事件 + 物资/计划维度 | `pick.py` |
| `story` **单机 3A 故事向**(大镖客、GTA) | ✅ 已验证 | **过场动画黑边** | `rdr_compose.py` |
| `shooter` 射击/竞技 | ⚠️ 未验证 | 瞬间反应,段落短 | `pick.py` |
| `action` **动作/武侠 ARPG**(影之刃、黑神话、只狼) | ⚠️ 未验证(2026-11 首用影之刃) | **战斗运动 + 剧情对白**双信号 | `pick.py` + `episodes.py` |

> `action` / `shooter` 开了 `motion_only`:**没有台词的战斗也会成为候选**(按运动 p85 +
  响度闸门)。为什么需要:精彩战斗往往一句话都没有,只从对白构造候选会把 BOSS 战全漏掉。
| `vlog` 旅行空镜 | ⚠️ 未验证 | 无解说 + 画面平稳 | `pick.py` |
| `talk` 口播/讲解 | ⚠️ 未验证 | 表达密度,优先"去废" | `pick.py` |

品类词典在 `scripts/genres.py`。**`story` 品类不要用 `pick.py`** —— 它的组装逻辑不同(过场优先),见下文。

## 核心原则(违反任何一条都会得到废结果)

1. **先确认品类,品类错了全白做**。同一段 118 秒对话:射击词典评"情绪 1.5"(判成又长又水),生存词典评"物资交易 35"(全场最值钱)。**开工第一件事是问品类。**
2. **先转写,再分析画面**。GPU 转写约 50 倍实时,画面逐帧分析只有 10~25 倍。文稿能直接定位"哪段有话可说",只对有内容的区间做画面分析。
3. **选段单元是「完整对话事件」,不是「单句爆点」**。用停顿切事件,阈值从数据里推(p93 分位)。把 3 分钟对话剁成 4 个 20 秒碎片是最常见的失败模式。
4. **绝不在句子中间切**。片段首尾落在完整句子边界上,留 0.5 秒呼吸余量。
5. **OBS 录像有 4 条音轨,而且不能用能量判据挑解说轨**。实测反例:轨1 能量 100% 却零中文字(纯音效),真正的解说在能量 54% 的轨上。判据只能用 **ASR 转写出的中文字密度**。
6. **"完整"和"节奏"是一对矛盾,要显式取舍**。保留完整对话会让静音占比到 50~83%(观感拖);压掉句间空隙能压缩约 46%。默认走 `tighten.py --mode gap`:只压空隙、不切语音。
7. **单机 3A 的故事骨架是过场动画黑边**(1920×1080 满幅 vs 816 有效高),不是解说密度。见 `references/gotchas.md` 第 0 条。
8. **游戏原声与解说分两条轨;音轨结构写错会被剪映静默丢弃**。用户要"两种声音都保留"时,做法是**视频轨带游戏原声 + 另一条 `type=audio` 轨放解说**(音量各自可调)。但这条音轨极易被**无声无息地丢掉** —— 草稿 JSON 里明明有两条轨、数据全部校验通过,剪映打开却只有一条,还不报任何错。原因是 `materials.audios[].type` 写成了非法的 `"audio"`(只能是 `music`)、片段缺 `extra_material_refs`(每个片段必须引用一个 `speeds` 素材)、字段不足 44 个。**遇到"数据对但剪映不认",先 diff 结构再怀疑数据** —— 标准答案在剪映自己产出的明文草稿里。见 `references/gotchas.md` 第 0c 条。

## 成片形态分档:短视频 / 中视频 / 长视频

同一个品类,做「60 秒切片」和做「20 分钟精华长片」的选段参数是**相反**的:
短切片要段短、点炸、几乎不留停顿;长片要段长、保叙事、允许留白。
档位在 `scripts/tiers.py` 里定义**一次**,`pick.py` / `tighten.py` / `rdr_compose.py` 共用。

| 档位 | 成片目标 | 预算 | 段长 | 单段上限 | 最多段数 | 压空隙 |
|---|---|---|---|---|---|---|
| `short` | 短视频切片(单条 ≤60s) | 60s(≤素材 25%) | 4~20s | 10s | 4 | 0.5s |
| `medium` | 中视频(3~8 分钟) | 300s(≤素材 40%) | 6~40s | 18s | 10 | 1.0s |
| `long` | 长视频(15~40 分钟) | 1200s(≤素材 55%) | 10~120s | 40s | 40 | 2.0s |
| `auto` | 按素材总长自动选:<3min→short / <30min→medium / 否则 long | — | — | — | — | — |

```bash
python scripts/pick.py --list-tiers                    # 打印全部分档
python scripts/pick.py --transcript t.json --metrics m.json --genre survival --tier short
python scripts/tighten.py --cuts x.cuts.json --transcript t.json --metrics m.json --tier long
python scripts/rdr_compose.py --work $W --file <base> --tier medium
```

**优先级:显式参数 > `--tier` > 品类默认**(三处脚本一致,`selftest.py` 有断言守着)。
`--tier auto` 自动选档;`--tier none` 或不给 = 沿用品类默认(向后兼容老命令)。

两个封顶别忽略:预算不会超过 `素材×档位比例`(10 分钟素材做不出 20 分钟长片),
短视频档的 60s 也只是上限而非承诺 —— 素材里没有 4 段像样的高光就只会出 1~2 段。

## 连续剧分集(长素材:45 小时素材 → 若干 13 分钟一集)

几十小时素材选出来的总时长往往是一集的好几倍,直接塞一个草稿就是缝合怪。
`episodes.py` 负责按「目标时长 ± 容差 + 自然断点」切成多集,每集各出一个草稿:

```bash
python scripts/episodes.py --cuts a.cuts.json --cuts b.cuts.json \
       --target-min 13 --tolerance-min 3 --min-min 6 \
       --out-dir work/episodes        # 产出 ep01.cuts.json / ep02.cuts.json + episodes.json
```

分集规则(为什么不是按秒硬切):
1. **优先在自然断点切**:同一素材内的长停顿、换文件(换场次)处;不在一场 BOSS 战中间断
2. **集长是区间**:13±3 分钟都算合格,在这区间里找最自然的断点
3. **宁可某集略长,也不要 2 分钟残集**:尾集不足 `--min-min` 会并入上一集
4. **集内保持时间顺序**:观众看的是"这一晚的精彩",不是打乱重排

## 叙事节拍:按剧情/情绪切,不按固定时长(2026-11 实战修正)

**踩坑**:第一版按"价值峰值"选段 + `tighten` 按句子切碎,一集 13 分钟里有
**96 个片段、中位段长 6.8 秒、53 处超过 1 分钟的跳变(最长跳 39.8 分钟)** ——
用户直接反馈"剪的太碎了,连续性有问题"。

**为什么不能靠"把每段拉长到固定 90 秒"修**:镜头长度应由内容决定。一句关键对白
+ 收束 30 秒就够;一场 BOSS 战从进场到残血反转可能 5 分钟。统一裁 90 秒既切碎长战斗,
又给短对白硬凑没意义的尾巴。

**正确做法:`beats.py` 找节拍边界,再按强度选节拍**

| 边界类型 | 判据 |
|---|---|
| 对白停顿 | 相邻台词间隔 >= 2.0s(一句话说完了) |
| 张力回落 | 运动+响度合成张力,连续 >=5s 低于 p30(战斗结束/镜头转平静) |
| 换场次 | 文件边界 |

节拍打分 = 词典维度/分钟 + 张力峰值 + **起势**(峰值与均值之差 = 有铺垫到爆发)+ 种子分。
实测 45.8h 素材切出 53 个诗拍(20 秒~9 分钟,中位 67 秒),再按强度选到 100 分钟、分 7 集。

```bash
# 单文件:切节拍
python scripts/beats.py --cuts x.cuts.json --metrics x.metrics.json        --transcript x.json --genre action --out x.beats.cuts.json
# 若只想把已有选段"连成块"(不重切),用 continuous.py
python scripts/continuous.py --cuts a.cuts.json --metrics a.metrics.json        --seed-top 72 --min-block 90 --max-block 170 --out a.cont.cuts.json
```

**验收判据**(别再只看总时长):一集的片段数、中位段长、段间跳变数。经验值:
每集 6~14 段、中位段长 60~180 秒、跳变处数 ≈ 段数-1 才算"能连着看"。

### 剧情对白:说话密度 + 画面字幕 OCR(2026-11 二次修正)

用户二次反馈「密集的剧情对话切得不够好」。查出两层原因:

1. **漏选**:动作游戏的剧情对白**没有战斗张力**,按分数排会被战斗挤掉 —— 实测一段
   78 秒连续对白(55:49–57:59)没进任何一集。修法:`beats.py` 增加「剧情对白场景」
   (台词间隔 ≤8s 连成串、语音占比 ≥45%、时长 ≥25s),并**按类型分配额**
   (`DLG_SHARE`,默认 55% 剧情 / 45% 战斗)。
2. **文本不可靠**:whisper 在安静对白上错字严重(「所谓正邪」→「所谓政协」),
   错字会拖低词典打分。修法:`subtitle_ocr.py` 用**画面内字幕 OCR**(RapidOCR)
   取干净文本与精确时间轴。为控成本,**先按说话密度圈定疑似对白区间再 OCR**
   (全片 OCR 要 11 小时;圈定后只占 15~30%)。

```bash
python scripts/subtitle_ocr.py --src a.mkv --transcript a.json --fps 0.5 \
       --out a.subs.json      # 输出对白段:start/end/text/lines
```

> 调研与选型(镜头边界 PySceneDetect / TransNetV2、叙事分段 TextTiling、音频事件 YAMNet)
> 见 `references/approaches.md`。

### 切点对齐镜头 + 话题分段(2026-11 三次修正)

节拍长度对了、切点位置也不一定对。又补了两层:

**A. 切点吸附到镜头边界**(`shots.py`)
- 影视剪辑的通行做法是"切在镜头切换处"(cut on cut):镜头自己换了,观众预期这里可以跳
- 实现:按 PySceneDetect `ContentDetector` 的思路(HSV 直方图差 + 边缘差,自适应阈值取局部峰)
  检测镜头切换;**只解码切点 ±6 秒的小窗口**(142 个切点约 1 分钟,不必全片再解一遍)
- 实测:142 个切点吸附 130 个(92%),平均位移 1.56 秒
- ⚠️ **必须按同一素材的时间序列逐一约束**,否则相邻片段会重叠(实测出现过 55 秒重叠)

**C. 话题分段**(`topics.py`,TextTiling / Hearst 1997)
- 用**汉字 bigram** 当伪词(不需要分词器),算相邻块的词汇余弦相似度,取 depth 局部峰为话题边界
- 用途:① 超长对白场景**按话题切**而不是等分;② 合并阶段**不跨话题边界**合并
  (实测:一段对话说完再跳到下一场,而不是切在话中间)

```bash
python scripts/topics.py --transcript a.json            # 只看话题边界
python scripts/shots.py --cuts beats.cuts.json --src-dir <素材目录> \
       --snap 6 --out beats.snapped.cuts.json           # 切点吸附
```

## 节奏控制(最常被投诉的问题)

选段为保完整性会保留大量停顿 —— **实测静音占比 49~83%**。用 `tighten.py` 压,
压多狠由档位决定(优先用 `--tier`,不要自己推 `--max-gap`):

```bash
python scripts/tighten.py --cuts x.cuts.json --transcript t.json --metrics m.json \
       --tier medium            # short 0.5s / medium 1.0s / long 2.0s

# 需要精调时再手写(显式参数压过档位)
python scripts/tighten.py ... --mode gap --max-gap 1.2 --min-seg 4
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--mode gap` | ✅ 推荐 | 只压超过 `--max-gap` 的空隙,不切语音 |
| `--max-gap` | 1.2 | 空隙上限。调小 → 更紧凑;0.8 很紧,2.0 接近原样 |
| `--min-seg` | 3.0 | 压缩后短于此的碎片会被丢掉 |
| `--mode hold` | ⚠️ 不推荐 | 固定留边,实测**必然切碎语音**(19/19 段) |

实测(13 分钟 MC,4 段选段):
```
272.6s -> 146.6s   压缩 46%   段数 4 -> 18
最长的段从 118.7s 降到 21.1s
静音占比 #2 从 49% 降到 25%
切点 18/18 落在句子边界
```

**取舍**:`gap` 模式会产生较多跳切(18 段而非 4 段)。这在"高光集锦"里是常规做法;若你要的是"完整叙事长片",就把 `--max-gap` 调到 2.0~3.0,只压掉最冗长的沉默。

## `story` 品类:单机 3A 故事向(独立流程)

**不要用 `pick.py`。** 这类素材的核心信号是**过场动画黑边**,组装逻辑也不同。

```bash
W=G:\autocut_work\<项目名>

# ① 挑轨并抽轨(游戏原声 en / 解说 zh 分开)
python scripts/batch_rdr.py --src <素材目录> --work $W

# ② 转写两条轨 + 画面分析
python scripts/rdr_stage2.py --src <素材目录> --work $W --stage all

# ③ 组装:过场优先全收 + 关键游玩段落补充
python scripts/rdr_compose.py --work $W --file <base> --ratio 0.20 --gameplay-ratio 0.08

# ④ 导出 + 生成草稿(带解说参考轨)
python scripts/batch_rdr_export.py --src <素材目录> --work $W
```

组装原则:

1. **过场动画是骨架,优先全收**(实测占素材 16%)。它是"故事"本身,不可替代。
2. **关键游玩段落是补充**,用过场之间的**游戏对白密度**定位 —— 能捞回"没有黑边但触发在
   游玩中的剧情演出"(实测捞回了任务中途的关键对白)。
3. 预算 `--ratio 0.20`,超出的过场按时长择优保留。

**你的解说如果被录得极轻**(实测轨道间差 70~85 dB),不要混入主音轨,用
`make_draft.py --extra-audio "0.0|<voice.wav>|解说参考"` 单独铺一条音频轨作参考。

**零过场/零对白的场次**(纯自由游玩)组装出 0 段 —— 此时改用**解说密度**当锚点重选。

## 品类细分怎么加(重要:不要新建 Skill)

**本 Skill 是一个大类,内部分五个品类。加品类请改 `scripts/genres.py`,不要新建 Skill。**

理由(想清楚再动):
- 管线代码(抽轨/转写/分析/导出/草稿)对各品类**完全相同**,只有打分词典、时长参数和组装策略不同。拆成多个 Skill = 把这些脚本复制 N 份,改一个 bug 要改 N 次。
- 一个 Skill 的 `description` 永久占用技能目录(实测约 150 token/个),正文只在加载时计入。所以拆分的代价不在 token,而在维护和路由。
- **路由靠猜 vs 靠问**:拆成多个 Skill 时,"帮我剪游戏录像"要靠关键词撞到正确那个 —— 撞错就是用错词典(这正是本项目踩过的坑:生存类素材被射击词典评成"又长又水")。一个 Skill 进来后明确问品类,不会错。

```bash
python scripts/pick.py --list          # 列出所有品类
python scripts/pick.py --transcript t.json --metrics m.json --genre survival
```

| 品类 | 状态 | 时长默认 | 组装 |
|---|---|---|---|
| `survival` | ✅ 已验证 | 15~120s | `pick.py` |
| `story` | ✅ 已验证 | 8~200s | **`rdr_compose.py`** |
| `shooter` | ⚠️ 未验证 | 8~45s | `pick.py` |
| `action` | ⚠️ 未验证(首用影之刃) | 8~60s | `pick.py` + `episodes.py` |
| `vlog` | ⚠️ 未验证 | 8~60s | `pick.py` |
| `talk` | ⚠️ 未验证 | 20~180s | `pick.py` |

**未验证的品类首次使用请小样抽检**,并核对输出的「未入选事件」清单。

## 跨 agent / 跨机器可用性

**技能装在哪决定谁能用。** 本技能装在 **`~/.agents/skills/video-autocut`(用户级根)**,
所以**同机器、同 Windows 账户下的任何 agent、任何工作区、任何会话(含 subagent)
都会自动发现它 —— 不需要导入,不需要复制。** 别去设置里点"导入技能":那是往
工作区级根写,只会多出一份副本。

发现根优先级(高→低),本机实测:
```
① <工作区>/.dsh/skills          ← 本机这个根里只有 browser-act
② <工作区>/.agents/skills       ← 本机不存在
③ $DSH_HOME/skills              ← DSH_HOME 是**按工作区隔离**的,本机没有 skills/
④ ~/.agents/skills              ← ★ 技能在这(注意 DSH_AGENTS_HOME 未设时才等于 ~/.agents)
⑤ 内置
```

### 但"能被发现" ≠ "能跑通"(2026-09 实修)

技能一度装着就能加载,但**脚本一跑就废** —— 因为脚本里写死了属于"上次运行那台
机器"的绝对路径。已修:

| 问题 | 影响面 | 修法 |
|---|---|---|
| `SKILL = r"C:\Users\...\.dsh\skills\..."` | 6 个批处理脚本 | 该目录**早已不存在**,子脚本全部找不到 → 改为 `os.path.dirname(os.path.abspath(__file__))` |
| `FFMPEG = (...WinGet\Packages...)` | **17 个脚本** | 写死某个用户的 winget 安装路径 → 统一走 `paths.ffmpeg()` |
| `DRAFT_ROOT` / 骨架路径写死 | `make_draft` `remove_draft` `install_testdraft` `validate_draft` | 改为 `paths.draft_root()` |

**换到别的机器/别的账户时,只需要跑一次自检:**

```bash
cd scripts
python paths.py            # 打印所有关键路径的解析结果 + 关键脚本是否齐全
python selftest.py         # 无需素材的端到端自检(三档差异/负分生效/组装)
```

`selftest.py` 用**合成文稿 + 合成画面指标**跑真脚本,断言 21 项:
三档预算/段数/段长单调、`--tier` 压过品类默认、显式参数压过档位、
负向维度真的扣分(talk 去废)、tighten 三档残留静音单调、story 档预算递增。
换机器/改脚本后先跑它,几秒钟就能知道有没有把选段逻辑弄坏。

`paths.py` 是**所有外部路径的唯一解析入口**,优先级:
环境变量(`JIANYING_DRAFT_ROOT` / `FFMPEG` / `AUTOCUT_FFMPEG`)> 系统默认位置
(`%LOCALAPPDATA%`)> 已知安装位置通配(`PATH` 里的、winget / scoop / choco)。
**新写脚本请用它,不要再写死绝对路径。**

其它 agent 上手顺序:`python paths.py` → 读 `SKILL.md` 品类表 → 问用户品类 →
按「固定流程」跑。

### 哪些脚本不是通用工具

`rebuild.py`、`rebuild_separate.py`、`build_masters.py`、`batch_rdr*.py`
是**某个具体批次**的一次性脚本,里面写死了该批次的 `G:\录像\...` 路径与
MC/RDR 配置。**它们是新批次的好模板,但不是开箱即用的工具** ——
用到别的素材上要先改配置段。

## 固定流程

```
① 探测素材       ffprobe 时长/分辨率/音轨数     → 确认有解说的素材有哪些
② 抽解说轨       extract_audio.py --track auto  → 每文件一个 wav + .track.json
③ GPU 转写       transcribe_long.py             → 带词级时间戳的 JSON(分块,防 MemoryError)
④ 画面分析       analyze.py --hw                → 逐秒运动/响度/静音
⑤ 完整事件选段   pick.py --genre <品类> --tier <档位> → 按词典打分,输出切点
⑥ 压紧节奏       tighten.py --tier <档位>       → 压掉句间空隙(完整性 vs 节奏)
⑦ 导出片段       cut_clips.py --track N         → 画面+解说轨合成 mp4(N 卡硬编)
⑧ 生成草稿       make_draft.py                  → 明文 JSON 草稿
⑨ 校验           validate_draft.py              → 三条 ID 链 + 时间线
⑩ 装入草稿库     install_when_closed.py         → 等剪映退出后自动装入
⑪ 清理中间产物   cleanup_work.py --dry / --yes  → 按清单回收(实测一次 258 GB)
```

### 文稿时间轴必须规整(`transcript.py`)

whisper 在**稀疏对白**素材上会把句子拉长 —— 实测影之刃有「3 个词跨 402 秒」、
单句最长 931 秒。不规整会直接导致:选段出现 181s/491s 的怪物候选段、压缩率失控、
分集时长统计错误。`transcript.py` 按字数反推合理时长做上界裁剪
(期望 ≈ 字数×0.45s+1,只裁明显异常的,正常句不误伤),`pick.py` / `tighten.py` /
`rdr_compose.py` 已统一接入 —— **新写读文稿的脚本请也用它,不要直接 json.load**。

每步的完整命令与参数见 `references/pipeline.md`,踩过的坑见 `references/gotchas.md`。

**要调具体某个脚本前,先查 `references/scripts.md`** —— 那是全部 49 个脚本的
自动生成索引(一句话说明 + 主要参数 + 分阶段归类),由 `gen_script_index.py`
从源码静态抽取,不会和代码脱节。想看某脚本的完整参数就 `python <脚本> --help`。

> 为什么需要它:SKILL.md 只有十几 KB,覆盖不了 49 个脚本的 CLI。没有索引时,
> 模型只能靠猜参数,或退化成"只用 SKILL.md 里出现过的那几个脚本"。
> 实测这份索引补上之前,**有 15 个脚本在任何文档里都没被提到过**
> (含 `analyze_tracks.py`「标准第一步」、`classify_voice.py`、`mix_audio.py`)。
> **改了脚本参数或说明,重跑 `python scripts/gen_script_index.py` 即可。**

## 清理中间产物(清单一处维护,不用每次翻目录)

**这条流水线会产出巨量中间件**(实测两个批次共 258 GB:分段视频、暂存副本、
分块转写、混合音轨)。清理规则固化在 **`work_manifest.json`**,不写在代码里:

```bash
cd scripts
python cleanup_work.py --dry        # 看将删什么、能回收多少
python cleanup_work.py --yes        # 真删
python cleanup_work.py --scan       # 自动发现新工作根并登记进清单
python cleanup_work.py --verify-only # 只校验素材完整性
```

**新批次开工时:先 `--scan` 登记工作根,再照格式补 `keep`/`remove` 规则。**
之后清理永远只是一条命令。

清单结构:每个 root 下 `keep` 是永不删的 glob、`remove` 是要删的 glob,
**keep 优先**;`drafts` 段管剪映草稿库;`media_check` 段在删之前校验成品素材完整。

三条安全设计(别绕过):

1. **先校验后删除**:删之前检查 `media_check.drafts` 里每个草稿引用的全部素材
   是否存在,缺一个就中止。这是防止"删掉唯一副本"的最后一道闸。
   (实测通过:`MC全库精华 914/914`、`RDR全库精华 473/473`)
2. **keep 优先于 remove**:两边都命中时保留,并把冲突打印出来。
3. **只删顶层项**:`remove` 的 glob 只在工作根第一层匹配,**不做递归通配**,
   避免 `**` 把整个盘卷进去。

**必须留的**(删了就得重跑几小时):
`mc/_game`、`dabiaoge/*.game.wav`、`dabiaoge/*.voice.wav`(音轨源)、
`_audio_final`(音轨判定结果)、各批次的 `*.cuts.json` / `*.json` 文稿与选段。
**可以放心删的**:`_clips*`、`_stage*`、`*.chunks`、`_mix*.m4a`、`masters/*_v2`、
`wheels`。成品草稿的素材在剪映库里自带 `_media/`,与工作目录无关。

> 旧的 `cleanup_intermediate.py` 已弃用并移到 `scripts/_archive/`(路径写死在
> `~\.dsh\skills`,且只处理草稿不管磁盘产物)。统一用 `cleanup_work.py`。
> 同理归档的还有 `_未验证_rank.py` / `_未验证_find_moments.py` / `_未验证_probe_mc.py`
> —— 它们曾被混在正式索引里,让模型误以为可用。

## 进度与规模参考(实测,44 小时 / 258 GB / 1080p60 HEVC)

| 步骤 | 速度 | 44 小时耗时 |
|---|---|---|
| 抽解说轨 | — | 8 分钟 |
| GPU 转写 | 50x 实时 | 53 分钟 |
| 画面分析(硬解) | 25 帧/秒 | 90 分钟 |
| 导出片段 | 6~9x 实时 | 按选中时长 |
| 生成+安装草稿 | 秒级 | — |

预算紧张时的顺序:**先只做 ①②③**(约 1 小时),拿到全库文稿地图再决定深挖哪些文件。

## 交付形态

默认交付**剪映草稿**(不是导出的成片),因为用户要的是"能接着剪的工程"。草稿要点:

- 主文件写 `draft_info.json`(剪映 6.0+;旧版 `draft_content.json` 会报"草稿内容已损坏")
- 必须同时写 `draft_meta_info.json`,并让 `materials.videos[].local_material_id` 指向 `draft_materials[type=0].value[].id` —— 漏了会报"媒体丢失",而且剪映的重新链接对话框修不好
- 素材复制进 `<草稿>/_media/` 使草稿自包含
- **加解说音轨时**:`materials.audios[].type` 必须写 `music`(不是 `audio`),每个片段必须引用一个 `speeds` 素材,音频段要写全 44 字段。**写错剪映会静默丢弃整条音轨,不报错**(见核心原则 8 / `gotchas.md` 0c)。存量草稿用 `fix_audio_track.py` 补救
- **写入前必须完全退出剪映**,否则被自动保存覆盖。用 `install_when_closed.py` 等待
- **长素材要做连续剧时**,先 `episodes.py` 分集再每集各出一个草稿(见上文「连续剧分集」)

**校验不能只查数据**:`validate_draft.py` 只证明 ID 链和三方时间线自洽,**证明不了剪映会渲染它**。加/改轨道后,若条件允许,应让用户实际打开确认;拿到"没有两条轨道"这类反馈时,先按 `gotchas.md` 0c 的清单 diff 剪映自己草稿的结构,不要先怀疑用户看错了。

草稿格式的全部细节与实测结论见 `references/jianying-draft.md`。

## 成片导出与发布(交付链路)

**裁剪清单 → 成片 mp4 → 平台适配版本**,全程不依赖剪映导出。

⚠️ **剪映没有可用的导出 CLI**:自带 `jianying-agent-cli-tests.exe` 只有
`draft edit / extract / apply-patch / validate-patch`,没有 export;GUI 导出无法可靠自动化。
所以成片由 `export_final.py` 从**同一份选段清单**直接渲染 —— 批量、可复现、参数可控。
代价:不带你在剪映里手动加的转场/文字(改过就用 `--source files` 让脚本只做响度与平台适配)。

```bash
# 渲染:默认输出到 <素材目录>/_autocut_export/<时间戳>/
python scripts/export_final.py --episodes work/episodes.json \
       --src-dir "G:\录像\影之刃" \
       --preset douyin,bilibili,youtube,archive

# 发布清单(半自动:生成标题/简介/标签 + 平台规格校验)
python scripts/publish.py --manifest "G:\录像\...\manifest.json" [--open]
```

平台预设与响度(实测标定,不是拍脑袋):

| 预设 | 分辨率 | 帧率 | 码率 | 响度 | 实测落地 |
|---|---|---|---|---|---|
| douyin | 1080x1920 竖屏(模糊背景填充) | 30 | 8 Mbps | 目标 I=-13 | **-13.9 LUFS / -1.1 dBTP** |
| bilibili | 1920x1080 | 60 | 10 Mbps | 同上 | -13.9 LUFS / -1.1 dBTP |
| youtube | 1920x1080 | 60 | 12 Mbps | 同上 | -13.9 LUFS / -1.1 dBTP |
| archive | 源分辨率 | 源 | HEVC cq20 | I=-15 | -15.4 LUFS / -1.5 dBTP |

**响度必须是"两遍法 + 限幅"**(踩坑记录):
- `loudnorm` 不传 `measured_*` 会走动态模式;传了但**漏掉 `offset`** 线性模式不生效
- `alimiter` 的 `level` 选项**默认 true**(自动电平),会整体再抬 2~3 dB —— 实测输出从 -14 变成
  -11.8 LUFS、真峰 +1.5 dBFS,越修越响。必须写 `level=false`
- AAC 编码会让真峰比样本峰高 1~2 dB,所以按目标 TP 再留 1.5~2 dB 余量 + 限幅器兜底

**上传能力边界(诚实说明)**:
- 全自动上传需要**平台开放平台授权**(B站/抖音/YouTube API)或带登录态的浏览器自动化;
  本机目前两者都没有,`publish.py` 默认做「元数据 + 规格校验 + 打开上传页」的**半自动**
- 凭据配置放 `<技能根>/publish_credentials.json`(勿提交),`publish.py --check-credentials` 可查

### 浏览器自动化投稿(`publish_browser.py`,2026-11 落地)

平台 API 要开发者资质(周期长),所以走**浏览器自动化复用已登录会话**:
Playwright 驱动本机 Chrome + **独立用户目录**(不碰日常浏览器),登录一次长期有效。

```bash
# ① 首次:打开三个平台上传页,让用户当场登录(窗口保留 2 小时)
python scripts/publish_browser.py --doctor --wait 7200

# ② 投一条:默认把文件/标题/简介/标签都填好,**停在发布按钮前**等人工确认
python scripts/publish_browser.py --platform bilibili \
       --file "G:\...\影之刃_EP01_B站_1920x1080.mp4" \
       --title "影之刃 | 高光节拍 第01集" --desc "..." --tags "影之刃,游戏剪辑"

# ③ 确认后用 --publish 真发;或按 manifest 批量
python scripts/publish_browser.py --platform bilibili --manifest "G:\...\manifest.json"
```

**为什么默认不点发布**:平台对自动化上传有风控(可能弹验证码/限流/触发审核),
把"最后一击"留给人是成本最低的稳妥做法。`--publish` 才真点。

**踩坑**:Playwright 用 `launch_persistent_context` + `channel="chrome"` 才能带登录态;
独立 profile 同一时间只能被一个进程占用 —— 登录窗口没关时跑投稿会卡在 profile 锁。

## 必须先问用户的三件事

1. **素材品类**:生存类 / 射击竞技 / vlog 空镜 / 口播讲解 —— 决定打分词典
2. **成片目标**:一堆独立高光短视频 / 一条完整精华长片 / 只要候选清单自己挑
3. **段落时长偏好**:保持完整对话(叙事完整但偏长)/ 只要价值峰值附近(紧凑但可能突兀)

不要跳过第 1 条。品类判断错了,后面全白做。

## 命名历史

本 Skill 原名 `survival-game-autocut`(起源于一个 Minecraft 素材项目),已于 2026-09 改名为 `video-autocut`,因为它的能力覆盖**五个**品类而非只有生存类。若在旧会话/旧笔记里见到旧名,指的是同一个 Skill。

**改 description 时的注意**:技能目录里只放 name + description,正文要主动加载。所以 description 是**唯一的路由依据** —— 漏一个品类,那个品类的素材就不会触发本技能(实测:早期版本写的是"生存/沙盒类、射击/竞技类",漏了 `story` 单机剧情向,拿着大镖客素材根本匹配不上)。**加品类时务必同步改 description。**
