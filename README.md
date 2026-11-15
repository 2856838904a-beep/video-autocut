# video-autocut

把**几十小时的长素材**变成「已挑好、已排好序、可继续编辑的剪映草稿」的自动化剪辑流水线。

它不是一个导出成片的工具 —— 它交付的是**工程**（剪映专业版草稿），你打开就能接着剪。

- 🎮 **五类素材**：生存/沙盒（MC、泰拉瑞亚）、单机 3A 故事向（大镖客、GTA）、射击竞技、vlog 空镜、口播讲解
- 📐 **三种成片形态**：短视频切片 / 中视频 / 长视频长片（`--tier short|medium|long|auto`）
- 🎧 **双音轨**：OBS 多轨录制自动识别游戏原声与解说，输出两条可各自调音量的轨道
- 💬 **剧情对白识别**：画面内字幕 OCR（rapidocr）+ 说话密度，解决密集对白「漏选/切碎」
- ⚡ **GPU 加速**：转写约 50× 实时，画面分析约 25 帧/秒，导出 6~9× 实时

本项目也是一个 **Agent Skill**（`SKILL.md` 是入口），可被 DeepSeek Harness / Claude 一类的
agent 自动发现并按其流程执行。

---

## 安装

### 作为 Agent Skill 使用（推荐）

技能放在**用户级**根目录，同机器同账户下的任何 agent / 会话都会自动发现，无需导入：

```bash
git clone https://github.com/2856838904a-beep/video-autocut.git ~/.agents/skills/video-autocut
```

Windows 下即 `C:\Users\<你>\.agents\skills\video-autocut`。

装好后让 agent「帮我剪游戏录像」即可，它会先问品类再动手。手工使用见下。

### 直接跑脚本

```bash
cd scripts
python paths.py        # 自检:ffmpeg / ffprobe / 剪映草稿库 / 关键脚本是否齐全
python selftest.py     # 无需素材的端到端自检(21 项断言)
```

## 环境依赖

| 依赖 | 说明 |
|---|---|
| Python 3.10+ | 开发环境为 3.13 |
| `numpy` | 抽轨 / 画面分析 / 选段都要 |
| `faster-whisper` | 转写（`transcribe_long.py`、`extract_audio.py --track auto`） |
| `torch` | 仅转写路径需要；N 卡请装 CUDA 版 |
| ffmpeg / ffprobe | 需要 `hevc_cuvid`（硬解）与 `h264_nvenc`（硬编），N 卡可用 |
| 剪映专业版 | 只有生成草稿这一步需要 |

```bash
pip install -r requirements.txt
# N 卡转写另按 pytorch 官网装对应 CUDA 版本
```

路径不写死：ffmpeg、剪映草稿库等全部由 `scripts/paths.py` 解析，支持环境变量
`FFMPEG` / `AUTOCUT_FFMPEG` / `JIANYING_DRAFT_ROOT` 覆盖。换机器先跑 `python paths.py`。

## 主流程

```
① 探测素材        analyze_tracks.py --src <素材目录>
② 抽解说轨        extract_audio.py --track auto
③ GPU 转写        transcribe_long.py          → 词级时间戳 JSON
④ 画面分析        analyze.py --hw             → 前后文/运动/响度/静音
⑤ 选段            pick.py --genre <品类> --tier <档位>
⑥ 压紧节奏        tighten.py --tier <档位>
⑥+ 连续剧分集     episodes.py --target-min 13(长素材必用)
⑦ 导出片段        cut_clips.py --cuts ... --track N
⑧ 生成草稿        make_draft.py               → draft_info.json + _media/
⑨ 校验            validate_draft.py           → 三条 ID 链 + 时间线
⑩ 装入草稿库      install_when_closed.py
⑪ 清理中间产物    cleanup_work.py --dry / --yes
```

`action` 品类(2026-11 首用影之刃):影之刃的过场是满幅 16:9、没有黑边,`detect_cutscenes.py` 抓不到,所以改用「对白密度≈剧情 / 画面运动≈战斗」双信号互补。

每步的完整命令与参数见 [`references/pipeline.md`](references/pipeline.md)，
46 个脚本的索引见 [`references/scripts.md`](references/scripts.md)（自动生成），
踩过的坑见 [`references/gotchas.md`](references/gotchas.md)。

## 成片形态分档

同一批素材，做 60 秒切片和做 20 分钟长片的选段参数是**相反**的。档位在
`scripts/tiers.py` 定义一次，`pick.py` / `tighten.py` / `rdr_compose.py` 共用。

| 档位 | 成片目标 | 预算 | 段长 | 单段上限 | 最多段数 | 压空隙 |
|---|---|---|---|---|---|---|
| `short` | 短视频切片（单条 ≤60s） | 60s（≤素材 25%） | 4~20s | 10s | 4 | 0.5s |
| `medium` | 中视频（3~8 分钟） | 300s（≤素材 40%） | 6~40s | 18s | 10 | 1.0s |
| `long` | 长视频（15~40 分钟） | 1200s（≤素材 55%） | 10~120s | 40s | 40 | 2.0s |
| `auto` | 按素材总长自动选 | — | — | — | — | — |

**优先级：显式参数 > `--tier` > 品类默认**，`selftest.py` 有断言守着这条。

```bash
python scripts/pick.py --list-tiers
python scripts/pick.py --transcript t.json --metrics m.json --genre survival --tier short
```

## 设计原则（违反任一条都会得到废结果）

1. **先确认品类**。同一段 118 秒对话：射击词典评「情绪 1.5」，生存词典评「物资交易 35」。
2. **先转写，再分析画面**。GPU 转写约 50× 实时，逐帧画面分析只有 10~25×。
3. **选段单元是「完整对话事件」，不是「单句爆点」**。把 3 分钟对话剁成 4 个 20 秒碎片是最常见的失败模式。
4. **绝不在句子中间切**。首尾落在完整句子边界，留呼吸余量。
5. **OBS 不能用能量判据挑解说轨**。实测轨 1 能量 100% 却零中文字（纯音效），真正解说在能量 54% 的轨上 —— 判据只能是 ASR 转写出的中文字密度。
6. **「完整」和「节奏」是矛盾，要显式取舍**。默认 `tighten.py --mode gap`：只压空隙、不切语音。
7. **负分必须真的扣分**。早期版本只累加正分，导致「口误/填充词 −3.5」完全失效（口播类"去废"空转）。现已修，并有自检断言。

## 实测规模参考（44 小时 / 258 GB / 1080p60 HEVC）

| 步骤 | 速度 | 44 小时耗时 |
|---|---|---|
| 抽解说轨 | — | 8 分钟 |
| GPU 转写 | 50× 实时 | 53 分钟 |
| 画面分析（硬解） | 25 帧/秒 | 90 分钟 |
| 导出片段 | 6~9× 实时 | 取决于选中时长 |
| 生成 + 安装草稿 | 秒级 | — |

预算紧张时先只做 ①②③（约 1 小时），拿到全库文稿地图再决定深挖哪些文件。

## 目录结构

```
SKILL.md                 Agent Skill 入口（品类表 / 流程 / 必问三件事）
README.md
scripts/                 46 个脚本
  paths.py               所有外部路径的唯一解析入口（先跑这个）
  selftest.py            无素材端到端自检（21 项断言）
  tiers.py               ★ 成片形态分档（短/中/长）
  genres.py              ★ 品类词典（加品类改这里，不要新建 Skill）
  pick.py / tighten.py / analyze.py / cut_clips.py / make_draft.py ...
  _archive/              已归档的未验证/已弃用脚本（别当正式工具）
references/
  pipeline.md            每步的完整命令与参数
  scoring.md             打分维度与档位权重
  gotchas.md             踩坑清单（音频轨静默丢失、草稿结构等）
  scripts.md             自动生成的脚本索引（gen_script_index.py）
  jianying-draft.md      剪映草稿格式实测细节
work_manifest.json       清理规则清单（keep/remove + 删前素材校验）
```

## 已知限制

- `shooter` / `vlog` / `talk` 三个品类的词典是**按品类特征推导**的，尚未用真实素材端到端验证；
  首次使用请小样抽检，并核对输出的「未入选事件」清单。`survival` / `story` 已验证。
- 生成草稿依赖 Windows + 剪映专业版；其余步骤跨平台。
- 写入草稿前必须完全退出剪映，否则会被自动保存覆盖（`install_when_closed.py` 会等）。
- 仓库只含代码与文档，**不含任何素材**。

## License

MIT — 见 [LICENSE](LICENSE)。
