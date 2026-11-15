# 归档脚本(不在正式流程里)

这些脚本**不要当正式工具用**,保留只为可追溯:

| 脚本 | 为什么归档 |
|---|---|
| `_未验证_rank.py` | 纯指标打分,已被 `pick.py --genre` 取代(品类词典更准) |
| `_未验证_find_moments.py` | 从文稿挑时段,已被 `pick.py` 的"完整事件切分"取代 |
| `_未验证_probe_mc.py` | 单次批次探测样例,日常直接 `ffprobe` / `analyze_tracks.py` |
| `cleanup_intermediate.py` | 已弃用:路径写死在 `~\.dsh\skills`,且只管草稿不管磁盘产物。统一用 `cleanup_work.py` |

归档前它们都躺在 `scripts/` 根下,`gen_script_index.py` 会把它们和正式工具
混在一起索引,导致模型误以为是可用工具。现在索引只覆盖 `scripts/*.py`,
本目录不参与生成。
