"""
tiers.py —— 成片形态分档(短视频 / 中视频 / 长视频)的唯一参数源

为什么需要这一层:
  同一个品类,做成「60 秒竖屏切片」和「20 分钟精华长片」的选段参数是**相反的**。
  短切片要:段短、点炸、几乎不留停顿、只要 1~4 个点。
  长片要:段长、保对话完整、允许留白、要几十个点。
  以前这些差异散落在各脚本的默认值和手工 --max-unit / --budget-sec 里,
  换个目标形态就要重新推一遍参数,而且三个脚本(tighten / rdr_compose)各调各的。

现在:成片形态只在这里定义一次,pick / tighten / rdr_compose 共用。

优先级(高→低),三处脚本一致:
  1. 命令行显式参数(python pick.py --max-unit 15)
  2. --tier 指定档位(本文件)
  3. 品类默认(genres.py)
  4. 脚本内置兜底

用法:
  python pick.py --transcript t.json --metrics m.json --genre survival --tier short
  python pick.py ... --tier auto        # 按素材总长自动选档
  python tiers.py                       # 打印全部分档
"""
from __future__ import annotations

# ============================================================ 分档定义
# budget_ratio_cap: 预算最多占素材总长的比例。
#   防止「10 分钟素材按长片档要 20 分钟成片」这种荒谬结果 —— 拿不出那么多好内容。
TIERS: dict[str, dict] = {
    "short": {
        "label": "短视频切片(单条 ≤60s,一条只讲一个点)",
        "why": "算法看前 3 秒、看完整播放率。所以:段短、开场即高光、几乎不留停顿。",
        "min": 4.0, "max": 20.0, "max_unit": 10.0,
        "budget_sec": 60.0, "budget_ratio_cap": 0.25,
        "top": 4, "pad": 0.25,
        "density_boost": 1.25,      # 更看重"单位时间信息量"
        "variety_boost": 0.55,      # 不要求维度丰富,单点炸就够
        "still_min_run": 4.0,       # vlog 空镜最短 4s(短切片可接受短空镜)
        "tighten": {"mode": "gap", "max_gap": 0.5, "min_seg": 2.0},
        "story": {"ratio": 0.06, "gameplay_ratio": 0.02, "cs_min": 4.0, "pad": 0.4},
        "note": "切点宁硬勿拖:句间空隙 >0.5s 就压掉。适合抖音/Shorts/视频号单条。",
    },
    "medium": {
        "label": "中视频(3~8 分钟,3~6 个高光串联)",
        "why": "B 站/YouTube 中段形态,要节奏但不能碎:每段能独立看懂,段间允许呼吸。",
        "min": 6.0, "max": 40.0, "max_unit": 18.0,
        "budget_sec": 300.0, "budget_ratio_cap": 0.40,
        "top": 10, "pad": 0.4,
        "density_boost": 1.0,
        "variety_boost": 1.0,
        "still_min_run": 6.0,
        "tighten": {"mode": "gap", "max_gap": 1.0, "min_seg": 2.5},
        "story": {"ratio": 0.12, "gameplay_ratio": 0.04, "cs_min": 5.0, "pad": 0.8},
        "note": "默认档。压掉 1s 以上的空隙,保留句间自然呼吸。",
    },
    "long": {
        "label": "长视频(15~40 分钟,完整精华长片)",
        "why": "叙事优先。观众愿意看长,但要求前后有因果:宁可留白,不要跳切。",
        "min": 10.0, "max": 120.0, "max_unit": 40.0,
        "budget_sec": 1200.0, "budget_ratio_cap": 0.55,
        "top": 40, "pad": 0.5,
        "density_boost": 0.90,      # 不追单位时间密度,追整体价值量
        "variety_boost": 1.05,
        "still_min_run": 10.0,
        "tighten": {"mode": "gap", "max_gap": 2.0, "min_seg": 3.0},
        "story": {"ratio": 0.20, "gameplay_ratio": 0.06, "cs_min": 6.0, "pad": 1.0},
        "note": "只压 2s 以上的冗长沉默,接近原样。要完整叙事就选这档。",
    },
}

# 未指定 --tier 时的行为:保持历史默认(各品类自己的 budget_ratio / min / max),
# 不强行套用某档,避免老命令行结果突变。
DEFAULT_TIER = None

# auto 档的分界(按素材总时长)
AUTO_SHORT_MAX = 180.0      # < 3 分钟素材 -> short
AUTO_MEDIUM_MAX = 1800.0    # < 30 分钟 -> medium;否则 long


def get(name: str) -> dict:
    if name not in TIERS:
        raise KeyError(f"未知成片档位 {name!r};可选:{', '.join(TIERS)}")
    return TIERS[name]


def auto(source_duration: float) -> str:
    """按素材总长自动选档 —— 素材太短做不出长片,太长全做切片又浪费。"""
    if source_duration < AUTO_SHORT_MAX:
        return "short"
    if source_duration < AUTO_MEDIUM_MAX:
        return "medium"
    return "long"


def budget_for(name: str | None, total: float, ratio: float) -> tuple[float, str]:
    """算成片预算(秒),返回 (预算, 说明)。

    无 tier:沿用品类 ratio 的老逻辑(max(200, 总长*ratio)),保持向后兼容。
    有 tier:用档位固定预算,但不超过 总长*budget_ratio_cap(素材不够就缩)。
    """
    legacy = max(200.0, total * ratio)
    if not name:
        return legacy, f"品类比例 {ratio:.0%}"
    t = get(name)
    capped = total * t["budget_ratio_cap"]
    if t["budget_sec"] <= capped:
        return t["budget_sec"], f"{name} 档固定 {t['budget_sec']:.0f}s"
    return capped, (f"{name} 档 {t['budget_sec']:.0f}s 超出素材可支撑上限,"
                    f"缩到 总长×{t['budget_ratio_cap']:.0%}={capped:.0f}s")


def pick_defaults(name: str | None) -> dict:
    """pick.py 用的档位默认值(与品类默认合并时,档位优先)。"""
    if not name:
        return {}
    t = get(name)
    return {k: t[k] for k in
            ("min", "max", "max_unit", "top", "pad",
             "density_boost", "variety_boost", "still_min_run") if k in t}


def tighten_defaults(name: str | None) -> dict:
    if not name:
        return {}
    return dict(get(name).get("tighten", {}))


def story_defaults(name: str | None) -> dict:
    if not name:
        return {}
    return dict(get(name).get("story", {}))


def list_tiers() -> str:
    lines = []
    for k, t in TIERS.items():
        lines.append(f"  {k:<8} {t['label']}")
        lines.append(f"           预算 {t['budget_sec']:.0f}s(≤素材 {t['budget_ratio_cap']:.0%})  "
                     f"段长 {t['min']:.0f}~{t['max']:.0f}s  单段上限 {t['max_unit']:.0f}s  "
                     f"最多 {t['top']} 段")
        lines.append(f"           压空隙 max_gap {t['tighten']['max_gap']}s   {t['note']}")
    lines.append(f"  auto     按素材总长自动选:"
                 f"<{AUTO_SHORT_MAX:.0f}s→short / "
                 f"<{AUTO_MEDIUM_MAX / 60:.0f}min→medium / 否则 long")
    return "\n".join(lines)


if __name__ == "__main__":
    print("成片形态分档:")
    print(list_tiers())
