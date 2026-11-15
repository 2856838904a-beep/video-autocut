"""
rdr_compose.py —— 大镖客故事向选段组装器

组装逻辑(按用户的"过场 + 关键游玩段落"):
  1. 过场动画(黑边)是剧情骨架 —— 默认全收,是每场的核心
  2. 关键游玩段落 —— 用三个信号定位:
       游戏对白密度(英文,剧情信息最多)
       你的解说密度(你关注的地方)
       画面运动(战斗/追逐等动作)
  3. 每段首尾留呼吸余量,不与过场重叠
  4. 预算 = 素材 × ratio(默认 0.20),过场优先占用,剩余给关键游玩

为什么过场优先:过场是"故事"本身,不可替代;游玩段落是补充上下文,
  预算冲突时应该牺牲后者。

用法:
  python rdr_compose.py --work <工作目录> --file <base> [--ratio 0.20]
                        [--gameplay-ratio 0.05] [--out x.cuts.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tiers
import transcript

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CJK = re.compile(r"[\u4e00-\u9fff]")

# 剧情向关键词(你的解说里出现这些 = 剧情相关内容)
STORY_KW = [
    (r"任务|剧情|主线|支线|接任务|交任务|过场|动画|剧情杀", 3.0),
    (r"这谁|他是谁|为什么|怎么会|什么情况|怎么回事|原来", 2.5),
    (r"杀了|打死|干掉|枪战|开枪|追|逃|跑|抓|救|偷袭|埋伏", 2.5),
    (r"帅|牛|厉害|绝了|卧槽|我操|牛逼|太强|离谱|帅爆", 2.0),
    (r"强盗|帮派|警长|赏金|通缉|火车|抢劫|营地|老大|牛仔|印第安", 2.5),
    (r"打猎|剥皮|马|换马|喂马|钓鱼|扎营|做饭|睡觉", 1.5),
    (r"买|卖|钱|美元|钱不够|赏金|商店|枪店|裁缝|理发", 2.0),
    (r"存档|读档|退回|重来|卡了|闪退|掉线", 1.5),
]


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 60:02d}:{t % 60:02d}"


def load_json(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None


def segs_of(p):
    j = load_json(p)
    if not j:
        return []
    s = j.get("segments") if isinstance(j, dict) else j
    s = transcript.clamp_segments(list(s or []))
    return [{"start": float(x["start"]), "end": float(x["end"]),
             "text": (x.get("text") or "").strip()} for x in s]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--file", required=True, help="素材 basename(不含扩展名)")
    ap.add_argument("--tier", default=None,
                    choices=["short", "medium", "long"],
                    help="成片形态:short 短视频切片 / medium 中视频 / long 长视频长片。"
                         "只补未显式指定的 --ratio/--gameplay-ratio/--cs-min/--pad。")
    ap.add_argument("--ratio", type=float, default=None, help="成片占素材比例(默认按档位/0.20)")
    ap.add_argument("--gameplay-ratio", type=float, default=None,
                    help="关键游玩段落额外占比(默认按档位/0.05)")
    ap.add_argument("--cs-min", type=float, default=None, help="过场最小时长(默认按档位/6.0)")
    ap.add_argument("--pad", type=float, default=None, help="首尾呼吸余量(默认按档位/1.0)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    # 档位只补没被显式指定的参数(命令行 > --tier > 内置默认)
    st = tiers.story_defaults(a.tier)
    ratio = a.ratio if a.ratio is not None else st.get("ratio", 0.20)
    g_ratio = a.gameplay_ratio if a.gameplay_ratio is not None else st.get("gameplay_ratio", 0.05)
    cs_min = a.cs_min if a.cs_min is not None else st.get("cs_min", 6.0)
    pad = a.pad if a.pad is not None else st.get("pad", 1.0)

    base = a.file
    cs = load_json(os.path.join(a.work, base + ".cutscenes.json"))
    mt = load_json(os.path.join(a.work, base + ".metrics.json"))
    voice = segs_of(os.path.join(a.work, base + ".voice.json"))
    game = segs_of(os.path.join(a.work, base + ".game.json"))

    if not mt:
        print(f"缺画面指标: {base}.metrics.json")
        return 2
    dur = mt["duration"]
    wins = mt["windows"]
    win = mt.get("window", 1.0)
    n = len(wins)
    mo = [w["motion"] for w in wins]
    mo_med = statistics.median(mo) if mo else 0.0
    mo_p90 = sorted(mo)[int(n * 0.9)] if n else 0.0

    budget = dur * ratio
    gbudget = dur * g_ratio

    tier_tag = f"[{a.tier} 档] " if a.tier else ""
    print(f"{tier_tag}素材 {fmt(dur)} ({dur / 60:.1f} 分)   预算 {budget / 60:.1f} 分 "
          f"(过场优先, {ratio:.0%}) + 关键游玩 {gbudget / 60:.1f} 分 ({g_ratio:.0%})")
    print(f"信号: 过场 {len(cs['cuts']) if cs else 0} 段   "
          f"解说 {len(voice)} 句   游戏对白 {len(game)} 句   运动中位 {mo_med:.0f}")

    # ---- 1) 过场:全收(按预算截断,长的优先) ----
    cs_cuts = []
    if cs:
        cs_cuts = [dict(c) for c in cs["cuts"] if c["dur"] >= cs_min]
        cs_cuts.sort(key=lambda c: -c["dur"])
    used = 0.0
    picked_cs = []
    for c in cs_cuts:
        if used + c["dur"] > budget:
            continue
        picked_cs.append(c)
        used += c["dur"]
    picked_cs.sort(key=lambda c: c["start"])
    cs_total = sum(c["dur"] for c in picked_cs)
    print(f"\n[过场] 收 {len(picked_cs)}/{len(cs_cuts)} 段,共 {cs_total / 60:.1f} 分")

    # ---- 2) 关键游玩段落 ----
    def motion_at(t):
        i = int(t // win)
        return mo[i] if 0 <= i < n else 0.0

    def in_cutscene(t):
        return any(c["start"] - 2 <= t <= c["end"] + 2 for c in picked_cs)

    # 逐句给"剧情价值"打分
    def score_line(txt, extra=0.0):
        s = extra
        for pat, w in STORY_KW:
            if re.search(pat, txt):
                s += w
        if len(txt) >= 10:
            s += 0.5
        return s

    # 以游戏对白(剧情信息最密)为主锚点,解说的中文作为补充
    anchors = []
    for s in game:
        sc = score_line(s["text"], extra=1.5)      # 对白本身有剧情价值
        if sc > 0:
            anchors.append({"t": (s["start"] + s["end"]) / 2, "score": sc,
                            "text": s["text"], "src": "对白"})
    for s in voice:
        cjk = len(CJK.findall(s["text"]))
        if cjk < 2:
            continue
        sc = score_line(s["text"], extra=0.5)
        if sc > 0:
            anchors.append({"t": (s["start"] + s["end"]) / 2, "score": sc,
                            "text": s["text"], "src": "解说"})

    # 对白密度:每 60 秒窗口内的对白句数 —— 密度高处剧情推进快
    dens = [0.0] * n
    for s in game:
        i0, i1 = int(s["start"] // win), int(s["end"] // win)
        for i in range(max(0, i0), min(n, i1 + 1)):
            dens[i] += 1
    # 窗口平滑
    sm = [0.0] * n
    for i in range(n):
        lo, hi = max(0, i - 15), min(n, i + 16)
        sm[i] = sum(dens[lo:hi]) / (hi - lo)

    # 生成候选段:以高密度区为骨架
    # ⚠️ 阈值不能太高。首版用「非零值中位数 × 1.5」,在剧情密集文件上算出 3.0,
    #    结果一段都选不出来(过场之间的游玩段落本来密度就低)。
    nz = [x for x in sm if x > 0]
    dens_thr = max(0.5, (statistics.median(nz) if nz else 1.0) * 0.8)
    print(f"  对白密度平滑后: 中位 {statistics.median(nz) if nz else 0:.2f}  "
          f"最大 {max(sm) if sm else 0:.2f}  阈值 {dens_thr:.2f}")
    hot = [i for i in range(n) if sm[i] >= dens_thr]
    raw = []
    cur = []
    for i in hot:
        if cur and i - cur[-1] <= 5:
            cur.append(i)
        else:
            if cur:
                raw.append(cur)
            cur = [i]
    if cur:
        raw.append(cur)

    gp = []
    for seg in raw:
        s0, s1 = seg[0] * win, (seg[-1] + 1) * win
        d = s1 - s0
        if d < 6:
            continue
        # 与过场重叠的部分剪掉(而不是整段丢弃)
        cut_spans = [(c["start"] - pad, c["end"] + pad) for c in picked_cs]
        parts = [(s0, s1)]
        for c0, c1 in cut_spans:
            new_parts = []
            for p0, p1 in parts:
                if c1 <= p0 or c0 >= p1:
                    new_parts.append((p0, p1))
                    continue
                if c0 > p0:
                    new_parts.append((p0, min(c0, p1)))
                if c1 < p1:
                    new_parts.append((max(c1, p0), p1))
            parts = new_parts
        for p0, p1 in parts:
            pd = p1 - p0
            if pd < 8:
                continue
            if pd > 120:
                p1 = p0 + 120
                pd = 120
            inner = [x for x in anchors if p0 - 2 <= x["t"] <= p1 + 2]
            val = sum(x["score"] for x in inner)
            pk = max((motion_at(p0 + k * 0.5) for k in range(int(pd * 2) + 1)), default=0)
            gp.append({"start": round(p0, 1), "end": round(p1, 1), "dur": round(pd, 1),
                       "value": round(val, 1), "peak_motion": round(pk, 1),
                       "lines": len(inner),
                       "text": " ".join(x["text"] for x in inner)[:150]})

    gp.sort(key=lambda x: -(x["value"] + x["peak_motion"] / 20.0))
    picked_gp, gused = [], 0.0
    for g in gp:
        if gused + g["dur"] > gbudget:
            continue
        picked_gp.append(g)
        gused += g["dur"]
    picked_gp.sort(key=lambda x: x["start"])
    print(f"[游玩] 候选 {len(gp)} 段,收 {len(picked_gp)} 段,共 {gused / 60:.1f} 分")

    # ---- 3) 合并、加呼吸余量 ----
    all_cuts = []
    for c in picked_cs:
        all_cuts.append({"start": c["start"], "end": c["end"], "kind": "过场",
                         "text": ""})
    for g in picked_gp:
        all_cuts.append({"start": g["start"], "end": g["end"], "kind": "游玩",
                         "text": g["text"]})
    all_cuts.sort(key=lambda c: c["start"])
    # 去重叠 + 加 pad
    merged = []
    for c in all_cuts:
        s0 = max(0.0, c["start"] - pad)
        s1 = min(dur, c["end"] + pad)
        if merged and s0 < merged[-1]["end"]:
            s0 = merged[-1]["end"]
        if s1 - s0 < 3:
            continue
        merged.append({"start": round(s0, 1), "end": round(s1, 1),
                       "dur": round(s1 - s0, 1), "kind": c["kind"],
                       "text": c["text"]})

    tot = sum(m["dur"] for m in merged)
    n_cs = sum(1 for m in merged if m["kind"] == "过场")
    n_gp = len(merged) - n_cs
    print(f"\n{'=' * 74}")
    print(f"总计 {len(merged)} 段 / {tot / 60:.1f} 分钟 "
          f"(占素材 {tot / dur * 100:.1f}%)  过场 {n_cs} 段  游玩 {n_gp} 段")
    print(f"{'=' * 74}")
    for i, m in enumerate(merged, 1):
        tag = "🎬" if m["kind"] == "过场" else "🎮"
        print(f"  #{i:>2} {tag} {fmt(m['start'])}–{fmt(m['end'])}  {m['dur']:6.1f}s  "
              f"{m['text'][:56]}")

    op = a.out or os.path.join(a.work, base + ".rdr.cuts.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump({"genre": "rdr_story", "tier": a.tier, "duration": dur,
                   "cuts": [{"start": m["start"], "end": m["end"], "dur": m["dur"],
                             "score": 0.0, "dims": {"过场" if m["kind"] == "过场" else "游玩": 1},
                             "text": m["text"], "kind": m["kind"]} for m in merged]},
                  f, ensure_ascii=False, indent=1)
    print(f"\n-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
