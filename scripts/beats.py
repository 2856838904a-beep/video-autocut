"""
beats.py —— 按「剧情 / 情绪推进」切分叙事节拍(不按固定时长)

★ 为什么固定时长不对(2026-11 影之刃实测):

  用户反馈"剪的太碎了,连续性有问题"。第一次修用「每段固定 90 秒」补上下文,
  方向对了一半 —— 但**镜头长度应该由内容决定,不该由参数决定**:
  一句关键对白 + 收束可能 40 秒就够;一场 BOSS 战从进场到残血反转可能 4 分钟;
  剧情演出从对话到情绪爆发可能 2 分钟。统一裁到 90 秒,既会切碎长战斗,
  也会给短对白硬凑无意义的尾巴。

  本脚本的做法是**找节拍边界**,而不是给长度:

    边界 = 内容"回落"的地方
      1. 对白停顿:相邻台词之间停顿 >= --pause(默认 2.0s)—— 一句话说完了
      2. 张力回落:运动 + 响度合成的张力,连续 >= --rest(默认 5s) 低于
         p30 —— 战斗结束/镜头切到平静处
      3. 换场次:文件边界一定是边界

    节拍 = 两条边界之间的一段。然后:
      · 按「叙事/情绪强度」给节拍打分(词典维度 + 张力峰值 + 情绪词)
      · 取分值高的节拍,直到预算
      · 相邻且都入选的节拍保持可选合并(间隔 <= --merge-gap)

  这样每个片段是"一件事"讲完:对话事件是完整的,战斗是打完的,
  长度天然 30 秒~5 分钟不等。

用法:
  python beats.py --cuts seeds.cuts.json --metrics m.json --transcript t.json \
         --budget-sec 6000 --out beats.cuts.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import genres
import transcript


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 60:02d}:{t % 60:02d}"


def tension_series(wins: list[dict]) -> list[float]:
    """把 运动 + 响度 合成一个每秒张力值(0~100)。"""
    mo = [w.get("motion", 0.0) for w in wins]
    lo = [w.get("loud") for w in wins]
    lo_vals = [x for x in lo if x is not None]
    mo_s = sorted(mo)
    p95 = mo_s[int(len(mo_s) * 0.95)] if mo_s else 1.0
    lo_s = sorted(lo_vals)
    lo_p10 = lo_s[int(len(lo_s) * 0.10)] if lo_s else -50.0
    lo_p95 = lo_s[int(len(lo_s) * 0.95)] if lo_s else -15.0
    out = []
    for i, w in enumerate(wins):
        m = min(1.0, w.get("motion", 0.0) / max(p95, 1e-6))
        l = w.get("loud")
        ln = 0.0 if l is None else max(0.0, min(1.0, (l - lo_p10) / max(lo_p95 - lo_p10, 1e-6)))
        out.append((m * 0.6 + ln * 0.4) * 100.0)
    return out


def text_of(window_segs: list[dict]) -> str:
    return " ".join(s["text"] for s in window_segs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuts", required=True, help="种子选段(pick.py 输出,用于确定要覆盖的热点)")
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--genre", default="action")
    ap.add_argument("--pause", type=float, default=2.0, help="对白停顿 >= 此值算节拍边界")
    ap.add_argument("--rest", type=float, default=5.0,
                    help="张力连续低于 p30 达此秒数算节拍边界(战斗结束)")
    ap.add_argument("--min-beat", type=float, default=20.0)
    ap.add_argument("--max-beat", type=float, default=300.0)
    ap.add_argument("--merge-gap", type=float, default=6.0,
                    help="相邻入选节拍间隔 <= 此值时连成一段")
    ap.add_argument("--budget-sec", type=float, default=0.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    prof = genres.get(a.genre)
    seeds = json.load(open(a.cuts, encoding="utf-8"))["cuts"]
    mp = json.load(open(a.metrics, encoding="utf-8"))
    wins = mp["windows"]
    total = float(mp["duration"])
    win = mp.get("window", 1.0)
    n = len(wins)
    _, segs = transcript.load(a.transcript, verbose=False)
    tens = tension_series(wins)

    # ---- 1) 找边界 ----
    # (a) 张力回落的"休息区"中心
    ts = sorted(tens)
    low_thr = ts[int(len(ts) * 0.30)] if ts else 0.0
    rest_secs = [i for i in range(n) if tens[i] <= low_thr]
    rests, run = [], []
    for i in rest_secs:
        if run and i - run[-1] <= 1:
            run.append(i)
        else:
            if run:
                rests.append(run)
            run = [i]
    if run:
        rests.append(run)
    boundaries = set()
    for r in rests:
        if len(r) * win >= a.rest:
            boundaries.add(int((r[0] + r[-1]) / 2))
    # (b) 对白停顿
    for i in range(1, len(segs)):
        gap = segs[i]["start"] - segs[i - 1]["end"]
        if gap >= a.pause:
            boundaries.add(int((segs[i - 1]["end"] + segs[i]["start"]) / 2))
    boundaries = sorted({b for b in boundaries if 0 <= b <= total})
    print(f"素材 {fmt(total)}   台词 {len(segs)} 句   节拍边界 {len(boundaries)} 个 "
          f"(张力回落 {sum(1 for r in rests if len(r) * win >= a.rest)} + 对白停顿)")

    # ---- 2) 种子 -> 节拍:把每个种子扩到"所在的整条节拍" ----
    def beat_around(t0: float, t1: float) -> tuple[float, float]:
        # 左边界:小于 t0 的最近边界(找不到就往后找,避免把节拍截断)
        left = max([b for b in boundaries if b <= t0], default=None)
        if left is None:
            left = min([b for b in boundaries if b >= t0], default=t0)
        right = min([b for b in boundaries if b >= t1], default=None)
        if right is None:
            right = max([b for b in boundaries if b <= t1], default=t1)
        left, right = float(left), float(right)
        if right - left > a.max_beat:
            # 超长节拍:以种子为中心截到最大时长,优先保留种子
            mid = (t0 + t1) / 2
            left = max(left, mid - a.max_beat / 2)
            right = min(right, left + a.max_beat)
        if right - left < a.min_beat:
            mid = (t0 + t1) / 2
            left = max(0.0, mid - a.min_beat / 2)
            right = min(total, left + a.min_beat)
        return round(left, 1), round(right, 1)

    beats_map: dict[tuple[float, float], dict] = {}
    for s in seeds:
        b0, b1 = beat_around(float(s["start"]), float(s["end"]))
        key = (b0, b1)
        b = beats_map.setdefault(key, {"start": b0, "end": b1, "dur": round(b1 - b0, 1),
                                       "seed_score": 0.0, "text": ""})
        b["seed_score"] = max(b["seed_score"], float(s.get("score") or 0.0))

    beats = sorted(beats_map.values(), key=lambda b: b["start"])

    # ---- 3) 打分:词典维度 + 张力峰值 + 情绪词(情绪递进) ----
    dims_cfg = prof["dims"]
    import re
    compiled = {k: (w, [re.compile(p) for p in pats]) for k, (w, pats) in dims_cfg.items()}

    def score_beat(b: dict) -> dict:
        t0, t1 = b["start"], b["end"]
        inner = [s for s in segs if s["end"] > t0 and s["start"] < t1]
        txt = text_of(inner)
        dims: dict[str, float] = {}
        for name, (w, pats) in compiled.items():
            if name == "表达密度":
                continue
            hits = sum(1 for p in pats if p.search(txt))
            if hits:
                dims[name] = round(hits * w, 2)
        i0, i1 = int(t0), min(n, int(t1) + 1)
        seg_t = tens[i0:i1] or [0.0]
        peak = max(seg_t)
        avg = sum(seg_t) / len(seg_t)
        # 情绪递进:峰值与均值的落差越大 = 这一段有"起势->爆发"
        rise = max(0.0, peak - avg)
        val = sum(v for v in dims.values() if v > 0)
        # 长节拍按分钟归一,避免"越长越赢"
        minutes = max(1.0, (t1 - t0) / 60.0)
        score = (val / minutes) * 1.0 + peak * 0.05 + rise * 0.08 + b["seed_score"] * 0.5
        b.update({"dims": dims, "text": txt[:200], "chars": len(txt),
                  "peak_tension": round(peak, 1), "avg_tension": round(avg, 1),
                  "rise": round(rise, 1), "score": round(score, 2)})
        return b

    beats = [score_beat(b) for b in beats]
    print(f"候选节拍 {len(beats)} 个,时长 中位 "
          f"{sorted(b['dur'] for b in beats)[len(beats)//2]:.0f}s")

    # ---- 4) 入选:按分数贪心,直到预算 ----
    chosen: list[dict] = []
    used = 0.0
    for b in sorted(beats, key=lambda x: -x["score"]):
        if b["dur"] < a.min_beat:
            continue
        if a.budget_sec and used + b["dur"] > a.budget_sec:
            continue
        chosen.append(b)
        used += b["dur"]

    # ---- 5) 相邻入选节拍合并(剧情连续) ----
    chosen.sort(key=lambda b: b["start"])
    merged: list[dict] = []
    for b in chosen:
        if merged and b["start"] - merged[-1]["end"] <= a.merge_gap:
            m = merged[-1]
            m["end"] = max(m["end"], b["end"])
            m["dur"] = round(m["end"] - m["start"], 1)
            m["text"] = (m["text"] + " " + b["text"]).strip()[:300]
            m["dims"] = {k: m.get("dims", {}).get(k, 0) + v
                         for k, v in b["dims"].items()}
            m["score"] = max(m["score"], b["score"])
        else:
            merged.append(dict(b))

    tot = sum(b["dur"] for b in merged)
    durs = sorted(b["dur"] for b in merged)
    print(f"入选 {len(chosen)} 节拍 -> 合并为 {len(merged)} 段 / {tot/60:.1f} 分钟")
    print(f"  段长 min {durs[0]:.0f}s / 中位 {durs[len(durs)//2]:.0f}s / "
          f"p90 {durs[int(len(durs)*0.9)]:.0f}s / max {durs[-1]:.0f}s")
    for b in merged:
        print(f"  {fmt(b['start'])}–{fmt(b['end'])} {b['dur']:6.1f}s "
              f"张力峰{b['peak_tension']:5.1f} 起势{b['rise']:4.1f} "
              f"{str(b['dims'])[:34]:<34} {b['text'][:26]}")

    op = a.out or os.path.splitext(a.cuts)[0] + ".beats.cuts.json"
    with open(op, "w", encoding="utf-8") as f:
        json.dump({"mode": "beats", "genre": a.genre,
                   "params": {"pause": a.pause, "rest": a.rest,
                              "min_beat": a.min_beat, "max_beat": a.max_beat,
                              "merge_gap": a.merge_gap, "budget_sec": a.budget_sec},
                   "cuts": [{**{k: v for k, v in b.items()}, "text": b.get("text", "")}
                            for b in merged]}, f, ensure_ascii=False, indent=1)
    print(f"-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
