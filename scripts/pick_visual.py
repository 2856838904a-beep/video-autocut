"""
pick_visual.py —— 纯画面选段(无解说素材专用)

适用场景:某些录像四轨 ASR 全部 0 字(真的没人说话,或麦克风没录上)。
这时文稿选段完全失效,只能靠画面运动找高光。

判据(全部来自 analyze.py 的逐秒指标):
  运动强度    高于中位数的持续区间 = 有事情在发生(跑图/战斗/建造)
  运动突变    突然飙升 = 瞬间高光(掉落/被偷袭/爆炸)
  镜头切换    cut 为真 = 界面切换/转场
  黑场/静音   扣分(转场空档、挂机)

与 pick.py 保持一致:输出同样的 cuts JSON 结构,便于后续 cut_clips / make_draft 复用。

用法:
  python pick_visual.py --metrics m.json [--transcript t.json]
                        [--top 12] [--budget-sec 300] [--max-unit 150]
                        [--out x.json]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 60:02d}:{t % 60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--transcript", default=None, help="可选:有文稿就拿来标注文字")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--budget-sec", type=float, default=300.0)
    ap.add_argument("--max-unit", type=float, default=150.0)
    ap.add_argument("--min-unit", type=float, default=6.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    m = json.load(open(a.metrics, encoding="utf-8"))
    wins = m["windows"]
    win = m.get("window", 1.0)
    n = len(wins)
    total = m["duration"]

    mo = [w["motion"] for w in wins]
    med = statistics.median(mo) if mo else 0.0
    p75 = sorted(mo)[int(n * 0.75)] if n else 0.0
    p95 = sorted(mo)[int(n * 0.95)] if n else 0.0

    print(f"素材 {fmt(total)}   窗口 {n} 个")
    print(f"运动: 中位 {med:.0f}  p75 {p75:.0f}  p95 {p95:.0f}  峰值 {max(mo) if mo else 0:.0f}")
    print(f"黑场占比 {m.get('dark_ratio', 0) * 100:.1f}%   静音占比 {m.get('silence_ratio', 0) * 100:.1f}%")

    # 逐窗口打分
    score = [0.0] * n
    for i in range(n):
        s = 0.0
        # 持续运动高于中位 -> 有事情在发生
        if med > 0:
            s += max(0.0, (mo[i] - med) / max(p95 - med, 1e-6)) * 50
        # 突变 -> 瞬间高光
        if i > 0:
            jump = mo[i] - mo[i - 1]
            if jump > 0:
                s += min(jump / 25.0, 1.0) * 30
        # 镜头切换 -> 界面/场景变化,通常伴随事件
        if wins[i].get("cut"):
            s += 15
        if wins[i].get("dark"):
            s -= 40
        if wins[i].get("silent"):
            s -= 10
        score[i] = s

    # 取高分连续区间
    thr = max(p75, med + 5)
    hot = [i for i in range(n) if score[i] >= thr * 0.6]
    raw: list[list[int]] = []
    cur: list[int] = []
    for i in hot:
        if cur and i - cur[-1] <= 3:
            cur.append(i)
        else:
            if cur:
                raw.append(cur)
            cur = [i]
    if cur:
        raw.append(cur)

    # 合并/裁剪到单位时长
    units: list[list[int]] = []
    for seg in raw:
        d = (seg[-1] - seg[0] + 1) * win
        if d < a.min_unit:
            continue
        if d <= a.max_unit:
            units.append(seg)
            continue
        # 按时长切
        step = int(a.max_unit / win)
        for k in range(0, len(seg), step):
            part = seg[k:k + step]
            if (part[-1] - part[0] + 1) * win >= a.min_unit:
                units.append(part)

    def unit_info(u: list[int]) -> dict:
        t0, t1 = u[0] * win, (u[-1] + 1) * win
        ws = [score[i] for i in u]
        pk = max(mo[i] for i in u)
        av = sum(mo[i] for i in u) / len(u)
        return {"start": round(t0, 1), "end": round(t1, 1),
                "dur": round(t1 - t0, 1), "score": round(sum(ws) / len(ws), 2),
                "peak_motion": round(pk, 1), "avg_motion": round(av, 1),
                "cuts": sum(1 for i in u if wins[i].get("cut"))}

    infos = sorted((unit_info(u) for u in units), key=lambda x: -x["score"])

    # 预算内挑选
    pick, used = [], 0.0
    for s in infos:
        if used + s["dur"] > a.budget_sec:
            continue
        pick.append(s)
        used += s["dur"]
        if len(pick) >= a.top:
            break
    pick.sort(key=lambda x: x["start"])

    # 有文稿时附带文字
    snip = {}
    if a.transcript and os.path.exists(a.transcript):
        tr = json.load(open(a.transcript, encoding="utf-8"))
        for s in (tr.get("segments") or []):
            snip[round(float(s["start"]), 1)] = (s.get("text") or "").strip()

    print(f"\n候选区间 {len(infos)} 个 -> 选中 {len(pick)} 段,共 {used:.0f}s "
          f"({used / 60:.1f} 分钟)")
    print("=" * 78)
    for i, s in enumerate(pick, 1):
        txt = ""
        if snip:
            inner = [t for k, t in snip.items() if s["start"] - 1 <= k <= s["end"]]
            txt = (" | " + " ".join(inner)[:70]) if inner else ""
        print(f"#{i:>2}  {fmt(s['start'])}–{fmt(s['end'])}  {s['dur']:6.1f}s  "
              f"运动峰 {s['peak_motion']:5.1f} (均 {s['avg_motion']:4.1f})  "
              f"切换 {s['cuts']:>2} 次{txt}")

    for s in pick:
        s["text"] = ""
    op = a.out or os.path.splitext(a.metrics)[0] + ".visual.cuts.json"
    with open(op, "w", encoding="utf-8") as f:
        json.dump({"genre": "visual", "duration": total,
                   "cuts": [{"start": s["start"], "end": s["end"], "dur": s["dur"],
                             "score": s["score"], "dims": {"画面运动": s["peak_motion"]},
                             "text": s.get("text", "")} for s in pick],
                   "all_units": infos}, f, ensure_ascii=False, indent=1)
    print(f"\n-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
