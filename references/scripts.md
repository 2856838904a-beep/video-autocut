# 脚本全索引

> **本文件由 `gen_script_index.py` 从源码静态生成,不要手改。**
> 改了脚本的参数或说明,重跑 `python gen_script_index.py` 即可。

模型加载技能后,`SKILL.md` 给出流程骨架,本文件给出**每个脚本的确切入口**。想看某个脚本的完整参数,直接 `python <脚本> --help`。

共 **43** 个脚本。

## 速查表

| 脚本 | 一句话 | 主要参数 |
|---|---|---|
| [`paths.py`](#pathspy) | paths.py —— 集中解析技能运行所需的外部路径 | — |
| [`selftest.py`](#selftestpy) | selftest.py —— 无需任何真实素材的端到端自检 | `--keep` |
| [`gen_script_index.py`](#gen-script-indexpy) | gen_script_index.py —— 自动生成 references/scripts.md(脚本全索引) | — |
| [`patch_paths.py`](#patch-pathspy) | patch_paths.py —— 一次性批量修复技能脚本里的失效/易失效硬编码路径 | — |
| [`extract_audio.py`](#extract-audiopy) | extract_audio.py —— 批量抽音轨(自动挑选解说轨) | `--src` `--dst` `--jobs` …(+1) |
| [`analyze_tracks.py`](#analyze-trackspy) | analyze_tracks.py —— 多音轨素材的音轨结构分析(标准第一步) | `--src` `--batch` `--out` |
| [`classify_voice.py`](#classify-voicepy) | classify_voice.py —— 判断解说轨是否「有效」 | `--work` `--out` `--limit` |
| [`pick_track_by_asr.py`](#pick-track-by-asrpy) | pick_track_by_asr.py —— 用「转写质量」挑解说轨(替代纯能量判据) | `--src` `--candidates` `--seconds` …(+3) |
| [`verify_tracks_all.py`](#verify-tracks-allpy) | verify_tracks_all.py —— 对全库每个文件逐轨转写验证,找出真正的解说轨 | `--src` `--seconds` `--out` |
| [`transcribe_long.py`](#transcribe-longpy) | transcribe_long.py —— 长素材分块转写(解决 6 小时音频一次性转写 MemoryError) | `--out` `--chunk-min` `--model` …(+4) |
| [`tiers.py`](#tierspy) | tiers.py —— 成片形态分档(短视频 / 中视频 / 长视频)的唯一参数源 | — |
| [`transcript.py`](#transcriptpy) | transcript.py —— 文稿加载 + 时间轴规整(所有选段类脚本的唯一入口) | — |
| [`analyze.py`](#analyzepy) | analyze.py —— 长素材技术质量分析(无需 GPU、无需转写) | `--out` `--window` `--fps` …(+3) |
| [`detect_cutscenes.py`](#detect-cutscenespy) | detect_cutscenes.py —— 检测过场动画区间(单机 3A 故事剪辑的核心信号) | `--src` `--fps` `--min` …(+3) |
| [`pick.py`](#pickpy) | pick.py —— 统一选段器(品类通过 --genre 切换,不再每个品类一个脚本) | `--transcript` `--metrics` `--genre` …(+13) |
| [`pick_visual.py`](#pick-visualpy) | pick_visual.py —— 纯画面选段(无解说素材专用) | `--metrics` `--transcript` `--top` …(+4) |
| [`beats.py`](#beatspy) | beats.py —— 按「剧情 / 情绪推进」切分叙事节拍(不按固定时长) | `--cuts` `--metrics` `--transcript` …(+8) |
| [`continuous.py`](#continuouspy) | continuous.py —— 把「碎片化选段」重建成「连续块」(解决观感"剪得碎、不连贯") | `--cuts` `--metrics` `--min-block` …(+6) |
| [`rdr_compose.py`](#rdr-composepy) | rdr_compose.py —— 大镖客故事向选段组装器 | `--work` `--file` `--tier` …(+5) |
| [`tighten.py`](#tightenpy) | tighten.py —— 压缩片段内部的对话空隙(解决"节奏太慢") | `--cuts` `--transcript` `--metrics` …(+7) |
| [`episodes.py`](#episodespy) | episodes.py —— 把选段切成一集一集的「连续剧」 | `--cuts` `--target-min` `--tolerance-min` …(+4) |
| [`cut_clips.py`](#cut-clipspy) | cut_clips.py —— 按切点导出片段(视频 + 指定解说音轨) | `--cuts` `--src` `--track` …(+9) |
| [`mix_audio.py`](#mix-audiopy) | mix_audio.py —— 把「游戏原声」和「解说」两条轨混成一条(两条都要保留) | `--game` `--voice` `--out` …(+7) |
| [`make_draft.py`](#make-draftpy) | make_draft.py —— 程序化生成剪映专业版草稿(纯明文 JSON,不需要解密) | `--name` `--clip` `--fps` …(+6) |
| [`merge_timelines.py`](#merge-timelinespy) | merge_timelines.py —— 把多个单时间线草稿合并成「一个总草稿 + 多条时间线」 | `--name` `--draft` `--timeline-name` …(+3) |
| [`validate_draft.py`](#validate-draftpy) | 校验生成的剪映草稿 —— 在丢给剪映之前把低级错误全挡掉。 | — |
| [`verify_master.py`](#verify-masterpy) | verify_master.py —— 校验「总草稿」全部时间线的素材可达性与 ID 链 | — |
| [`fix_audio_track.py`](#fix-audio-trackpy) | 把音轨修成剪映真正认的结构。 | — |
| [`install_when_closed.py`](#install-when-closedpy) | install_when_closed.py —— 等剪映主程序退出后自动安装草稿 | `--timeout` `--poll` |
| [`install_testdraft.py`](#install-testdraftpy) | 把暂存草稿安装进剪映草稿库。 | — |
| [`remove_draft.py`](#remove-draftpy) | remove_draft.py —— 安全删除剪映草稿(可恢复) | `--list` `--name` `--pattern` …(+2) |
| [`cleanup_work.py`](#cleanup-workpy) | 按 work_manifest.json 清理自动剪辑中间产物 | `--dry` `--yes` `--scan` …(+2) |
| [`show_transcript.py`](#show-transcriptpy) | show_transcript.py —— 人肉抽查转写结果(判断解说轨是不是挑错了) | — |
| [`per_track.py`](#per-trackpy) | per_track.py —— 逐条音轨分析(关键修正) | — |
| [`genres.py`](#genrespy) | genres.py —— 剪辑品类词典(细分小类的唯一数据源) | — |
| [`batch_all.py`](#batch-allpy) | batch_all.py —— 全库批处理:转写 → 画面分析 → 选段 → 压紧 → 导出 → 生成草稿 | `--src` `--audio` `--work` …(+7) |
| [`batch_visual.py`](#batch-visualpy) | batch_visual.py —— 对无语音素材跑「纯画面选段 → 导出 → 草稿」 | `--src` `--audio` `--work` …(+4) |
| [`batch_rdr.py`](#batch-rdrpy) | batch_rdr.py —— 荒野大镖客(单机 3A 故事向)全流程 | `--src` `--work` `--budget-ratio` …(+3) |
| [`batch_rdr_export.py`](#batch-rdr-exportpy) | batch_rdr_export.py —— 大镖客全库导出 + 生成草稿(含解说参考轨) | `--src` `--work` `--stage-root` …(+3) |
| [`rdr_stage2.py`](#rdr-stage2py) | rdr_stage2.py —— 大镖客第二阶段:转写解说轨 + 抽写游戏对白 + 画面分析 | `--src` `--work` `--stage` …(+2) |
| [`build_masters.py`](#build-masterspy) | build_masters.py —— 直接从片段目录建两个总草稿(不做单草稿) | `--batch` `--plan` `--master-root` …(+1) |
| [`rebuild.py`](#rebuildpy) | rebuild.py —— 按"音轨结构分析 + 解说质量判定"重建两批成片 | `--batch` `--plan` `--only` …(+2) |
| [`rebuild_separate.py`](#rebuild-separatepy) | rebuild_separate.py —— 最终重建:游戏原声与游玩解说分成两条轨 | `--batch` `--plan` `--only` …(+1) |

## 环境 / 自检

### paths.py

paths.py —— 集中解析技能运行所需的外部路径

直接运行即自检: `python paths.py`

### selftest.py

selftest.py —— 无需任何真实素材的端到端自检

| 参数 | 说明 | 默认 |
|---|---|---|
| `--keep` | 保留临时目录 | false |

### gen_script_index.py

gen_script_index.py —— 自动生成 references/scripts.md(脚本全索引)

**无 argparse,参数走位置参数。** 用法:

```
python gen_script_index.py            # 生成 references/scripts.md
```

### patch_paths.py

patch_paths.py —— 一次性批量修复技能脚本里的失效/易失效硬编码路径

(无命令行参数 —— 作为模块被其它脚本 import,
或直接运行执行固定动作)

## ② 抽音轨

### extract_audio.py

extract_audio.py —— 批量抽音轨(自动挑选解说轨)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--src` |  |  |
| `--dst` |  |  |
| `--jobs` |  | `1` |
| `--track` | auto = 按活跃度自动挑解说轨;也可以给具体索引如 3 | `auto` |

## ③ 音轨结构分析 / 质量判定

### analyze_tracks.py

analyze_tracks.py —— 多音轨素材的音轨结构分析(标准第一步)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--src` |  |  |
| `--batch` |  | false |
| `--out` |  |  |

### classify_voice.py

classify_voice.py —— 判断解说轨是否「有效」

| 参数 | 说明 | 默认 |
|---|---|---|
| `--work` |  |  |
| `--out` |  |  |
| `--limit` | 每文件最多分析多少秒 | `900.0` |

### pick_track_by_asr.py

pick_track_by_asr.py —— 用「转写质量」挑解说轨(替代纯能量判据)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--src` |  |  |
| `--candidates` | 按能量筛前几条轨去试转写 | `3` |
| `--seconds` | 每条轨每段样本秒数 | `90` |
| `--model` |  | `small` |
| `--device` |  | `cuda` |
| `--out` |  |  |

### verify_tracks_all.py

verify_tracks_all.py —— 对全库每个文件逐轨转写验证,找出真正的解说轨

| 参数 | 说明 | 默认 |
|---|---|---|
| `--src` |  |  |
| `--seconds` |  | `40` |
| `--out` |  | `_track_verify.json` |

## ④ GPU 转写

### transcribe_long.py

transcribe_long.py —— 长素材分块转写(解决 6 小时音频一次性转写 MemoryError)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--out` |  |  |
| `--chunk-min` |  | `20.0` |
| `--model` |  | `small` |
| `--device` |  | `cuda` |
| `--compute-type` |  |  |
| `--language` |  |  |
| `--vad` |  | `True` |

## ⑤ 选段前置:成片分档 + 文稿时间轴规整

### tiers.py

tiers.py —— 成片形态分档(短视频 / 中视频 / 长视频)的唯一参数源

(无命令行参数 —— 作为模块被其它脚本 import,
或直接运行执行固定动作)

### transcript.py

transcript.py —— 文稿加载 + 时间轴规整(所有选段类脚本的唯一入口)

(无命令行参数 —— 作为模块被其它脚本 import,
或直接运行执行固定动作)

## ⑥ 画面分析 / 选段 / 组装

### analyze.py

analyze.py —— 长素材技术质量分析(无需 GPU、无需转写)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--out` |  |  |
| `--window` |  | `1.0` |
| `--fps` |  | `4.0` |
| `--no-video` |  | false |
| `--audio` | 复用已抽好的 wav(转写也用同一份),避免为响度/静音再解码一次长视频 |  |
| `--hw` | 用 N 卡硬件解码(hevc_cuvid 等),实测快 2.6 倍 | false |

### detect_cutscenes.py

detect_cutscenes.py —— 检测过场动画区间(单机 3A 故事剪辑的核心信号)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--src` |  |  |
| `--fps` |  | `1.0` |
| `--min` |  | `8.0` |
| `--max` |  | `0.0` |
| `--min-bar-px` | 换算到原始分辨率后的黑边高度阈值(像素) | `40` |
| `--out` |  |  |

### pick.py

pick.py —— 统一选段器(品类通过 --genre 切换,不再每个品类一个脚本)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--transcript` |  |  |
| `--metrics` |  |  |
| `--genre` |  | `survival` |
| `--tier` | 成片形态:short 短视频切片 / medium 中视频 / long 长视频 / auto 按素材总长自动选 / none 沿用品类默认(不给就是 none)。显式参数(如 - |  |
| `--list` | 列出所有品类词典后退出 | false |
| `--list-tiers` | 列出成片分档后退出 | false |
| `--top` | 最多选几段(默认按档位/8) |  |
| `--gap` |  |  |
| `--split-gap` |  |  |
| `--min` |  |  |
| `--max` |  |  |
| `--budget-ratio` | 成片总长占素材比例(默认用品类值,生存类 0.35) |  |
| `--budget-sec` | 成片总长硬上限(秒)。给了就覆盖 --budget-ratio;超预算的单个事件会先按呼吸口切分,保证不超 |  |
| `--max-unit` | 单个片段的时长上限(秒)。超过就逐级降低停顿阈值继续切,保证段长有上界(默认取 --max 的 1/3)。调小 → 段更碎更均匀;调大 → 保留长对话 |  |
| `--pad` | 首尾呼吸余量(秒)。默认按档位(short 0.25 / medium 0.4 / long 0.5) |  |
| `--out` |  |  |

### pick_visual.py

pick_visual.py —— 纯画面选段(无解说素材专用)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--metrics` |  |  |
| `--transcript` | 可选:有文稿就拿来标注文字 |  |
| `--top` |  | `12` |
| `--budget-sec` |  | `300.0` |
| `--max-unit` |  | `150.0` |
| `--min-unit` |  | `6.0` |
| `--out` |  |  |

### beats.py

beats.py —— 按「剧情 / 情绪推进」切分叙事节拍(不按固定时长)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--cuts` | 种子选段(pick.py 输出,用于确定要覆盖的热点) |  |
| `--metrics` |  |  |
| `--transcript` |  |  |
| `--genre` |  | `action` |
| `--pause` | 对白停顿 >= 此值算节拍边界 | `2.0` |
| `--rest` | 张力连续低于 p30 达此秒数算节拍边界(战斗结束) | `5.0` |
| `--min-beat` |  | `20.0` |
| `--max-beat` |  | `300.0` |
| `--merge-gap` | 相邻入选节拍间隔 <= 此值时连成一段 | `6.0` |
| `--budget-sec` |  | `0.0` |
| `--out` |  |  |

### continuous.py

continuous.py —— 把「碎片化选段」重建成「连续块」(解决观感"剪得碎、不连贯")

| 参数 | 说明 | 默认 |
|---|---|---|
| `--cuts` |  |  |
| `--metrics` | 与 --cuts 一一对应的 metrics.json(用于找安静切点) |  |
| `--min-block` |  | `75.0` |
| `--max-block` |  | `180.0` |
| `--merge-gap` | 两块间隔 <= 此值时连成一块(而不是跳过) | `15.0` |
| `--snap` | 切点吸附搜索半径(秒) | `8.0` |
| `--budget-sec` | 总时长上限;超过时按块价值裁掉(0=不限) | `0.0` |
| `--seed-top` | 先按分数取前 N 个选段当「场景中心」再展开(0=全部展开)。为什么需要:把 300 个 8 秒碎片全展开成 75 秒再裁掉大半纯属浪费,正确做法是先少选种子,再让每颗种子长成一段 | `0` |
| `--out` |  |  |

### rdr_compose.py

rdr_compose.py —— 大镖客故事向选段组装器

| 参数 | 说明 | 默认 |
|---|---|---|
| `--work` |  |  |
| `--file` | 素材 basename(不含扩展名) |  |
| `--tier` | 成片形态:short 短视频切片 / medium 中视频 / long 长视频长片。只补未显式指定的 --ratio/--gameplay-ratio/--cs-min/--pa |  |
| `--ratio` | 成片占素材比例(默认按档位/0.20) |  |
| `--gameplay-ratio` | 关键游玩段落额外占比(默认按档位/0.05) |  |
| `--cs-min` | 过场最小时长(默认按档位/6.0) |  |
| `--pad` | 首尾呼吸余量(默认按档位/1.0) |  |
| `--out` |  |  |

## ⑦ 压紧节奏

### tighten.py

tighten.py —— 压缩片段内部的对话空隙(解决"节奏太慢")

| 参数 | 说明 | 默认 |
|---|---|---|
| `--cuts` |  |  |
| `--transcript` |  |  |
| `--metrics` |  |  |
| `--mode` | gap=只压空隙(推荐) / hold=固定留边(会切碎语音) |  |
| `--tier` | 成片形态:short 短视频(压到 0.5s 空隙) / medium 中视频(1.0s) / long 长视频(2.0s,接近原样) |  |
| `--max-gap` | gap 模式:句间空隙超过此值就压到它(默认按档位/1.2) |  |
| `--hold` | hold 模式:每句前后固定保留的秒数 | `0.35` |
| `--min-seg` | 压缩后短于此的碎片丢弃(默认按档位/3.0) |  |
| `--merge-gap` | 两个结果段间隔小于此值时合并 | `0.8` |
| `--out` |  |  |

## ⑧ 连续剧分集(长素材必用)

### episodes.py

episodes.py —— 把选段切成一集一集的「连续剧」

| 参数 | 说明 | 默认 |
|---|---|---|
| `--cuts` | 可多次给:每个素材一个 cuts.json(顺序即播放顺序) |  |
| `--target-min` |  | `13.0` |
| `--tolerance-min` |  | `3.0` |
| `--min-min` | 一集的下限;尾集不足此值会尝试并入上一集 | `6.0` |
| `--gap-split` | 同一素材内,段间停顿 >= 此值视为自然断点(秒) | `15.0` |
| `--out-dir` |  |  |
| `--dry` | 只打印分集计划 | false |

## ⑨ 导出片段 / 混音

### cut_clips.py

cut_clips.py —— 按切点导出片段(视频 + 指定解说音轨)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--cuts` |  |  |
| `--src` |  |  |
| `--track` | 【游戏原声】所在的绝对音轨号(stream 索引,不是 a:N) | `3` |
| `--a-index` | 游戏原声在音频流里的序号(a:N 的 N)。默认由 --track 换算 |  |
| `--voice-wav` | 【解说】的独立 wav。给了就把游戏原声与解说混成一条音轨 —— 两条都保留。不给则片段里只有游戏原声 |  |
| `--voice-gain` | 解说提升量(dB)。默认按实测电平自动算(目标中位 -14 dBFS) |  |
| `--game-gain` | 游戏原声增益(dB),默认 -3 给解说让位 |  |
| `--no-denoise` | 不对解说做降噪 | false |
| `--voice-separate` | 【分离模式】视频片段只带游戏原声,解说导出为同名 .voice.m4a 独立文件 —— 用于在剪映里把解说放单独一条音频轨,可分别调音量 | false |
| `--dst` |  |  |
| `--crf` |  | `20` |
| `--limit` | 只导前 N 个(调试用) | `0` |

### mix_audio.py

mix_audio.py —— 把「游戏原声」和「解说」两条轨混成一条(两条都要保留)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--game` |  |  |
| `--voice` |  |  |
| `--out` |  |  |
| `--voice-target` | 解说的目标中位电平(dBFS)。默认 -14,比游戏原声略高以保证听得清 |  |
| `--game-gain` | 游戏原声的增益(dB)。默认 -3,给解说让出空间 |  |
| `--max-boost` | 解说最大提升量(dB),防止把纯底噪放大过头 | `40.0` |
| `--no-denoise` |  | false |
| `--no-compress` |  | false |
| `--target-lufs` | 可选:整体响度归一化目标(EBU R128) |  |
| `--bitrate` |  | `192k` |

## ⑩ 生成草稿

### make_draft.py

make_draft.py —— 程序化生成剪映专业版草稿(纯明文 JSON,不需要解密)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--name` |  |  |
| `--clip` |  |  |
| `--fps` |  | `30.0` |
| `--out-root` |  |  |
| `--no-inline` | 不把素材复制进草稿目录(默认会复制,使草稿自包含) | false |
| `--media-src` | 素材的原始位置(单个文件路径)。生成到暂存目录时会用到 |  |
| `--final-root` | 草稿库**根目录**(不是草稿文件夹本身)。脚本会自动在其下拼 <--name>。先在暂存区生成、之后整体搬进草稿库时用它一次写对路径。例:--out-root D:\tmp -- |  |
| `--extra-audio` | 额外的音频参考轨,格式 起始秒/文件/显示名(起始秒可省,默认 0)。用于把音量很小的解说轨单独铺一条轨作参考,不混入主音轨 |  |
| `--clip-voice` | 逐片段的解说音频,与 --clip 一一对应(顺序必须一致)。给了就建一条「游玩解说」独立音频轨,视频轨只含游戏原声 |  |

### merge_timelines.py

merge_timelines.py —— 把多个单时间线草稿合并成「一个总草稿 + 多条时间线」

| 参数 | 说明 | 默认 |
|---|---|---|
| `--name` | 总草稿名 |  |
| `--draft` | 源草稿目录(每个变成一条时间线),可重复 |  |
| `--timeline-name` | 与 --draft 一一对应的显示名;留空则用源草稿名 |  |
| `--out-root` | 生成到哪里(暂存区) |  |
| `--final-root` | 草稿最终位置;给了就按它写路径,方便之后整体搬走 |  |
| `--dry-run` |  | false |

## ⑪ 校验 / 修复

### validate_draft.py

校验生成的剪映草稿 —— 在丢给剪映之前把低级错误全挡掉。

**无 argparse,参数走位置参数。** 用法:

```
python validate_draft.py <草稿目录|draft_info.json>
```

### verify_master.py

verify_master.py —— 校验「总草稿」全部时间线的素材可达性与 ID 链

**无 argparse,参数走位置参数。** 用法:

```
python verify_master.py <草稿目录>
```

### fix_audio_track.py

把音轨修成剪映真正认的结构。

**无 argparse,参数走位置参数。** 用法:

```
python fix_audio_track.py <草稿目录> [--dry] [--also-content]
```

## ⑫ 装入草稿库

### install_when_closed.py

install_when_closed.py —— 等剪映主程序退出后自动安装草稿

| 参数 | 说明 | 默认 |
|---|---|---|
| `--timeout` | 最多等多少秒 | `1800` |
| `--poll` |  | `3.0` |

### install_testdraft.py

把暂存草稿安装进剪映草稿库。

**无 argparse,参数走位置参数。** 用法:

```
python install_testdraft.py <草稿目录>
```

### remove_draft.py

remove_draft.py —— 安全删除剪映草稿(可恢复)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--list` |  | false |
| `--name` |  |  |
| `--pattern` | 通配符,如 'MC*' |  |
| `--yes` | 跳过确认 | false |
| `--restore` |  |  |

## ⑬ 清理

### cleanup_work.py

按 work_manifest.json 清理自动剪辑中间产物

| 参数 | 说明 | 默认 |
|---|---|---|
| `--dry` | 只看不删(默认行为) | false |
| `--yes` | 真删 | false |
| `--scan` | 扫描并登记新工作根 | false |
| `--verify-only` | 只校验素材完整性 | false |
| `--skip-verify` | 跳过素材完整性校验(不推荐) | false |

## 辅助工具(查看中间结果)

### show_transcript.py

show_transcript.py —— 人肉抽查转写结果(判断解说轨是不是挑错了)

**无 argparse,参数走位置参数。** 用法:

```
python show_transcript.py <transcript.json>
```

### per_track.py

per_track.py —— 逐条音轨分析(关键修正)

(无命令行参数 —— 作为模块被其它脚本 import,
或直接运行执行固定动作)

## 品类词典(加品类改这里,不要新建 Skill)

### genres.py

genres.py —— 剪辑品类词典(细分小类的唯一数据源)

(无命令行参数 —— 作为模块被其它脚本 import,
或直接运行执行固定动作)

## 批次专用(不是通用工具,配置段写死了本批次路径;只用它们当模板)

### batch_all.py

batch_all.py —— 全库批处理:转写 → 画面分析 → 选段 → 压紧 → 导出 → 生成草稿

| 参数 | 说明 | 默认 |
|---|---|---|
| `--src` |  |  |
| `--audio` |  |  |
| `--work` |  |  |
| `--genre` |  | `survival` |
| `--skip` |  |  |
| `--stage` |  | `all` |
| `--max-gap` |  | `1.2` |
| `--top` |  | `8` |
| `--budget-ratio` | 每场成片总长占素材比例(默认 0.15 = 15%%)。按各文件实际时长自动折算成秒数,作为硬上限传给 pick.py | `0.15` |
| `--max-unit` | 单个片段时长上限(秒,默认 150)。超了就按呼吸口/强制切分,保证各场次节奏均匀 | `150.0` |

### batch_visual.py

batch_visual.py —— 对无语音素材跑「纯画面选段 → 导出 → 草稿」

| 参数 | 说明 | 默认 |
|---|---|---|
| `--src` |  |  |
| `--audio` |  |  |
| `--work` |  |  |
| `--files` |  |  |
| `--budget-sec` |  | `240.0` |
| `--max-unit` |  | `100.0` |
| `--top` |  | `14` |

### batch_rdr.py

batch_rdr.py —— 荒野大镖客(单机 3A 故事向)全流程

| 参数 | 说明 | 默认 |
|---|---|---|
| `--src` |  |  |
| `--work` |  |  |
| `--budget-ratio` |  | `0.2` |
| `--top` |  | `24` |
| `--max-unit` |  | `180.0` |
| `--stage` |  | `all` |

### batch_rdr_export.py

batch_rdr_export.py —— 大镖客全库导出 + 生成草稿(含解说参考轨)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--src` |  |  |
| `--work` |  |  |
| `--stage-root` | 草稿生成到哪里(默认 <work>/_stage) |  |
| `--final-root` |  |  |
| `--skip` |  |  |
| `--no-voice-track` | 不生成解说参考轨 | false |

### rdr_stage2.py

rdr_stage2.py —— 大镖客第二阶段:转写解说轨 + 抽写游戏对白 + 画面分析

| 参数 | 说明 | 默认 |
|---|---|---|
| `--src` |  |  |
| `--work` |  |  |
| `--stage` |  | `all` |
| `--fps` |  | `1.0` |
| `--chunk-min` |  | `20.0` |

### build_masters.py

build_masters.py —— 直接从片段目录建两个总草稿(不做单草稿)

| 参数 | 说明 | 默认 |
|---|---|---|
| `--batch` |  |  |
| `--plan` |  |  |
| `--master-root` | 总草稿建在哪(必须与片段同卷才能硬链接) |  |
| `--only` |  |  |

### rebuild.py

rebuild.py —— 按"音轨结构分析 + 解说质量判定"重建两批成片

| 参数 | 说明 | 默认 |
|---|---|---|
| `--batch` |  |  |
| `--plan` |  |  |
| `--only` | 只处理名字含此子串的文件 |  |
| `--limit` |  | `0` |
| `--dry-run` |  | false |

### rebuild_separate.py

rebuild_separate.py —— 最终重建:游戏原声与游玩解说分成两条轨

| 参数 | 说明 | 默认 |
|---|---|---|
| `--batch` |  |  |
| `--plan` |  |  |
| `--only` |  |  |
| `--dry-run` |  | false |

