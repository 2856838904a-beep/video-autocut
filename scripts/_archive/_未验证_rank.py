"""
rank.py —— 从技术指标里挑出「高光候选段」和「废片段」

针对 MC 游戏录像的特点设计(和 vlog 不同):
  - MC 没有镜头切换,cut 几乎恒为 0,所以不能用"切换点"找高光
  - 真正的信号是:运动突然飙升(打架/跑图/掉下去)、解说密度升高
  - 废片主要是:长时间无解说(挂机/挖矿) + 画面静止类

三种目标 profile:
  game   游戏高光:动作激烈 + 有解说
  vlog   空镜/风景:画面好看、稳定、无废帧
  talk   口播/解说:语音连续、无长停顿、无口误重来

用法:
  python rank.py <metrics.json> [--profile game] [--top 20] [--min-seg 8] [--max-seg 60]
                 [--transcript t.json] [--out candidates.json]
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
    return f"{t // 3600}:{(t % 3600) // 60:02d}:{t % 60:02d}"


def load_transcript(path: str | None) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    j = json.load(open(path, encoding="utf-8"))
    segs = j.get("segments") if isinstance(j, dict) else j
    out = []
    for s in segs or []:
        out.append({"start": float(s["start"]), "end": float(s["end"]),
                    "text": (s.get("text") or "").strip()})
    return out


def speech_by_window(tr: list[dict], n: int, win: float) -> tuple[list[float], list[str]]:
    """每个窗口的语音字数 + 文本片段。"""
    chars = [0.0] * n
    texts: list[list[str]] = [[] for _ in range(n)]
    for s in tr:
        a = max(0, int(s["start"] // win))
        b = min(n - 1, int(s["end"] // win))
        if b < a:
            continue
        share = len(s["text"]) / max(1, b - a + 1)
        for i in range(a, b + 1):
            chars[i] += share
            if s["text"] and len(texts[i]) < 2:
                texts[i].append(s["text"])
    return chars, [" ".join(t) for t in texts]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("metrics")
    ap.add_argument("--profile", default="game",
                    choices=["game", "vlog", "talk"])
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-seg", type=float, default=6.0)
    ap.add_argument("--max-seg", type=float, default=45.0)
    ap.add_argument("--transcript", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    m = json.load(open(a.metrics, encoding="utf-8"))
    wins = m["windows"]
    win = m.get("window", 1.0)
    n = len(wins)
    dur = m["duration"]

    mo = [w["motion"] for w in wins]
    lo = [w["loud"] if w["loud"] is not None else -60.0 for w in wins]
    silent = [bool(w["silent"]) for w in wins]

    mo_med = statistics.median(mo) if mo else 0.0
    mo_p90 = sorted(mo)[int(n * 0.9)] if n else 0.0
    lo_med = statistics.median(lo) if lo else -60.0

    tr = load_transcript(a.transcript)
    chars, snippet = ([0.0] * n, [""] * n)
    if tr:
        chars, snippet = speech_by_window(tr, n, win)
    chars_med = statistics.median([c for c in chars if c > 0]) if any(chars) else 0.0

    # ---- 逐窗口打分 ----
    score = [0.0] * n
    for i in range(n):
        s = 0.0
        # 运动:相对该素材的中位数与 p90
        if mo_p90 > mo_med:
            s += max(0.0, (mo[i] - mo_med) / (mo_p90 - mo_med)) * 45
        # 运动突变(高光往往是一瞬间的爆发)
        if i > 0:
            jump = mo[i] - mo[i - 1]
            if jump > 0:
                s += min(jump / 30.0, 1.0) * 25
        if a.profile == "game":
            if silent[i]:
                s -= 25
            if lo[i] > lo_med:
                s += 10
            if chars[i] > chars_med:
                s += 20
        elif a.profile == "vlog":
            # 空镜:要稳定、不糊、有声音(环境声/配乐)
            if mo[i] > mo_p90 * 1.6:
                s -= 20          # 剧烈晃动扣分
            if 0.5 * mo_med < mo[i] < mo_p90:
                s += 25          # 平滑推移最佳
            if silent[i]:
                s -= 10
            s += 15
        else:  # talk
            if chars[i] > chars_med:
                s += 40
            if silent[i]:
                s -= 35
        score[i] = s

    # ---- 合并成连续区间 ----
    thr = max(20.0, sorted(score)[int(n * 0.85)] if n else 20.0)
    hot = [i for i in range(n) if score[i] >= thr]
    segs: list[dict] = []
    cur: list[int] = []
    for i in hot:
        if cur and i - cur[-1] <= 2:
            cur.append(i)
        else:
            if cur:
                segs.append({"a": cur[0], "b": cur[-1]})
            cur = [i]
    if cur:
        segs.append({"a": cur[0], "b": cur[-1]})

    # 展开/收缩到合理时长,合并后打分
    merged: list[dict] = []
    for sg in segs:
        a_i, b_i = sg["a"], sg["b"]
        want = int(a.max_seg / win) if hasattr(a, "max_seg") else int(a.max_seg / win)
        # 往前后扩展到目标时长
        target = min(max(int(a.min_seg / win), b_i - a_i + 1), int(a.max_seg / win))
        pad = (target - (b_i - a_i + 1)) // 2
        a_i = max(0, a_i - pad)
        b_i = min(n - 1, a_i + target - 1)
        t0, t1 = a_i * win, (b_i + 1) * win
        if merged and t0 - merged[-1]["end"] < 3:
            merged[-1]["end"] = t1
            merged[-1]["wins"].extend(range(a_i, b_i + 1))
            continue
        merged.append({"start": t0, "end": t1, "wins": list(range(a_i, b_i + 1))})

    out = []
    for sg in merged:
        ws = [w for w in sg["wins"] if 0 <= w < n]
        if not ws:
            continue
        peak_mo = max(mo[w] for w in ws)
        avg_s = sum(score[w] for w in ws) / len(ws)
        talk = sum(chars[w] for w in ws)
        sil = sum(1 for w in ws if silent[w]) / len(ws)
        text = " ".join(dict.fromkeys(s for s in (snippet[w] for w in ws) if s))[:160]
        out.append({
            "start": round(sg["start"], 1),
            "end": round(sg["end"], 1),
            "dur": round(sg["end"] - sg["start"], 1),
            "score": round(avg_s, 1),
            "peak_motion": round(peak_mo, 1),
            "talk_chars": int(talk),
            "silence_ratio": round(sil, 2),
            "text": text,
        })

    out.sort(key=lambda x: -x["score"])
    out = out[: a.top]

    # ---- 废片 ----
    waste: list[dict] = []
    run_a = None
    for i in range(n + 1):
        is_w = i < n and silent[i] and mo[i] < max(8.0, mo_med * 0.5)
        if is_w and run_a is None:
            run_a = i
        elif not is_w and run_a is not None:
            if (i - run_a) * win >= 20:
                waste.append({"start": round(run_a * win, 1),
                              "end": round(i * win, 1),
                              "dur": round((i - run_a) * win, 1),
                              "reason": "无解说且画面静止"})
            run_a = None
    waste.sort(key=lambda x: -x["dur"])

    waste_total = sum(w["dur"] for w in waste)

    # ---- 报告 ----
    print(f"素材时长   {fmt(dur)}  ({dur / 3600:.2f} 小时)")
    print(f"profile    {a.profile}")
    print(f"运动       中位 {mo_med:.0f}  p90 {mo_p90:.0f}  峰值 {max(mo) if mo else 0:.0f}")
    print(f"响度       中位 {lo_med:.1f} dBFS")
    print(f"静音占比   {m.get('silence_ratio', 0) * 100:.1f}%")
    if tr:
        print(f"转写       {len(tr)} 句  {sum(len(s['text']) for s in tr)} 字")
    else:
        print("转写       无(--transcript 未提供,只按画面+响度选)")
    print(f"\n废片       {len(waste)} 段,共 {fmt(waste_total)} ({waste_total / 60:.1f} 分钟)")
    for w in waste[:5]:
        print(f"   {fmt(w['start'])}–{fmt(w['end'])}  {w['dur'] / 60:5.1f} 分钟  {w['reason']}")

    print(f"\n高光候选 {len(out)} 段:")
    for i, c in enumerate(out, 1):
        print(f"\n  #{i}  {fmt(c['start'])}–{fmt(c['end'])}  时长 {c['dur']:5.1f}s  "
              f"推荐 {c['score']:5.1f}  运动峰值 {c['peak_motion']:5.1f}  "
              f"语音 {c['talk_chars']:4d} 字  静音 {c['silence_ratio'] * 100:3.0f}%")
        if c["text"]:
            print(f"       解说: {c['text']}")

    res = {"source": a.metrics, "duration": dur, "profile": a.profile,
           "stats": {"motion_median": mo_med, "motion_p90": mo_p90,
                     "loud_median": lo_med, "silence_ratio": m.get("silence_ratio"),
                     "has_transcript": bool(tr)},
           "waste": waste, "candidates": out}
    op = a.out or os.path.splitext(a.metrics)[0] + ".candidates.json"
    with open(op, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
