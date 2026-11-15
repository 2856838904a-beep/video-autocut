"""
tighten.py —— 压缩片段内部的对话空隙(解决"节奏太慢")

问题:为了"对话完整性",选段保留了事件内的全部停顿 —— 但实测静音占比
      高达 50~83%,也就是说片段里一多半时间没人说话,观感自然拖。

数据(13 分钟 MC 素材的 4 段选段):
  #1 74.0s  静音 59%
  #2 118.7s 静音 49%
  #3 51.3s  静音 78%
  #4 28.6s  静音 83%   ← 28 秒里 23 秒没人说话

★ 模式选择(实测结论):
  gap  —— 【推荐】只压掉超过阈值的句间空隙,不切任何语音。
          实测:273s -> 147s(压缩 46%),18 段,起点/终点 100% 落在句子边界
  hold —— ⚠️ 不推荐。按"每句前后固定留 N 秒"压缩,实测 19/19 段全部切在
          句子中间 —— 因为固定留边无法与句长对齐。仅在你明确要极紧凑、
          接受切碎语音时使用。

★ 按成片形态分档(短/中/长视频压法不同,细节见 tiers.py):
  short   压掉 >0.5s 的空隙 —— 短视频要"句句接上",节奏最紧
  medium  压掉 >1.0s 的空隙 —— 中视频默认,保留自然呼吸
  long    压掉 >2.0s 的空隙 —— 长视频只削冗长沉默,接近原样
  不给 --tier 则沿用 --max-gap 1.2 的历史默认。

用法:
  python tighten.py --cuts cuts.json --transcript t.json --metrics m.json
                    [--tier short|medium|long] [--mode gap] [--max-gap 1.2] [--out x.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tiers
import transcript

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 60:02d}:{t % 60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuts", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--mode", choices=["gap", "hold"], default=None,
                    help="gap=只压空隙(推荐) / hold=固定留边(会切碎语音)")
    ap.add_argument("--tier", default=None,
                    choices=["short", "medium", "long"],
                    help="成片形态:short 短视频(压到 0.5s 空隙) / "
                         "medium 中视频(1.0s) / long 长视频(2.0s,接近原样)")
    ap.add_argument("--max-gap", type=float, default=None,
                    help="gap 模式:句间空隙超过此值就压到它(默认按档位/1.2)")
    ap.add_argument("--hold", type=float, default=0.35,
                    help="hold 模式:每句前后固定保留的秒数")
    ap.add_argument("--min-seg", type=float, default=None,
                    help="压缩后短于此的碎片丢弃(默认按档位/3.0)")
    ap.add_argument("--merge-gap", type=float, default=0.8,
                    help="两个结果段间隔小于此值时合并")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    # 档位只补"没被显式指定"的参数:命令行 > --tier > 内置默认
    td = tiers.tighten_defaults(a.tier)
    mode = a.mode or td.get("mode") or "gap"
    max_gap = a.max_gap if a.max_gap is not None else td.get("max_gap", 1.2)
    min_seg = a.min_seg if a.min_seg is not None else td.get("min_seg", 3.0)

    cuts = json.load(open(a.cuts, encoding="utf-8"))["cuts"]
    tr, segs = transcript.load(a.transcript, verbose=True)
    mp = json.load(open(a.metrics, encoding="utf-8"))
    wins = mp["windows"]
    win = mp.get("window", 1.0)
    n = len(wins)
    tr_dur = float(mp.get("duration") or (n * win))

    def silence_ratio(t0: float, t1: float) -> float:
        i0, i1 = int(t0 // win), int(t1 // win)
        if i1 <= i0:
            return 0.0
        rng = range(max(0, i0), min(n, i1))
        if not rng:
            return 0.0
        return sum(1 for i in rng if wins[i]["silent"]) / len(rng)

    print(f"模式 {mode}", end="")
    if mode == "gap":
        print(f"(压掉 > {max_gap}s 的句间空隙)")
    else:
        print(f"(每句前后各留 {a.hold}s)")
    print()

    out_cuts = []
    total_before = 0.0
    total_after = 0.0

    for ci, c in enumerate(cuts, 1):
        c0, c1 = c["start"], c["end"]
        total_before += c1 - c0
        inner = [s for s in segs if s["end"] > c0 and s["start"] < c1]
        # 首句开头 / 末句结尾也要贴合片段边界
        if inner:
            inner[0] = dict(inner[0], start=max(inner[0]["start"], c0))
            inner[-1] = dict(inner[-1], end=min(inner[-1]["end"], c1))

        # ★ 无语音片段(纯战斗/空镜)原样保留:没有句子 = 没有空隙可压。
        #   原代码会让 keeps 为空 -> final 为空 -> 整段被丢掉。
        #   实测影之刃:动作类选段里近一半是无台词的战斗段,不修会把最精彩的
        #   部分全删掉(49.4 分钟被压成 23.7 分钟,丢的正是战斗)。
        if not inner:
            k0, k1 = max(c0, 0.0), min(c1, tr_dur)
            total_after += k1 - k0
            out_cuts.append({
                "start": round(k0, 2), "end": round(k1, 2),
                "dur": round(k1 - k0, 2),
                "src_start": round(c0, 2), "src_end": round(c1, 2),
                "score": c.get("score"), "text": c.get("text", ""),
                "speechless": True,
            })
            print(f"#{ci}  {fmt(c0)}–{fmt(c1)}  {c1 - c0:6.1f}s 无语音 -> 原样保留")
            continue

        keeps: list[list[float]] = []
        if mode == "gap":
            cur = None
            for s in inner:
                if cur is None:
                    cur = [s["start"], s["end"]]
                    continue
                hole = s["start"] - cur[1]
                if hole <= max_gap:
                    cur[1] = s["end"]
                else:
                    keeps.append(cur)
                    cur = [s["start"], s["end"]]
            if cur:
                keeps.append(cur)
        else:  # hold
            for s in inner:
                keeps.append([s["start"] - a.hold, s["end"] + a.hold])

        # 合并过近的 keeps
        merged: list[list[float]] = []
        for k in keeps:
            if merged and k[0] - merged[-1][1] < a.merge_gap:
                merged[-1][1] = max(merged[-1][1], k[1])
            else:
                merged.append(list(k))

        # 丢掉过短的碎片
        final = [k for k in merged if k[1] - k[0] >= min_seg]
        # 若过滤后为空(整段都很碎),退回保留最长的一个
        if not final and merged:
            final = [max(merged, key=lambda k: k[1] - k[0])]
        # 不越过原片段边界
        for k in final:
            k[0] = max(c0, k[0])
            k[1] = min(c1, k[1])

        after = sum(k[1] - k[0] for k in final)
        total_after += after
        sr_before = silence_ratio(c0, c1)
        sr_after = (sum(silence_ratio(k[0], k[1]) for k in final) / len(final)
                    if final else 0.0)

        print(f"#{ci}  {fmt(c0)}–{fmt(c1)}  {c1 - c0:6.1f}s 静音 {sr_before * 100:3.0f}%"
              f"   ->  {len(final)} 段 {after:6.1f}s 静音 {sr_after * 100:3.0f}%"
              f"   压缩 {100 * (1 - after / max(c1 - c0, 1e-6)):3.0f}%")
        for k in final:
            out_cuts.append({
                "start": round(k[0], 2), "end": round(k[1], 2),
                "dur": round(k[1] - k[0], 2),
                "src_start": round(c0, 2), "src_end": round(c1, 2),
                "score": c.get("score"), "text": c.get("text", ""),
            })

    # 跨原片段之间也去重(压缩后可能与下一段重叠)
    out_cuts.sort(key=lambda x: x["start"])
    dedup = []
    for k in out_cuts:
        if dedup and k["start"] < dedup[-1]["end"]:
            k["start"] = dedup[-1]["end"]
            k["dur"] = round(k["end"] - k["start"], 2)
            if k["dur"] < min_seg:
                continue
        dedup.append(k)

    print(f"\n合计 {total_before:.1f}s -> {total_after:.1f}s  "
          f"(压缩 {100 * (1 - total_after / max(total_before, 1e-6)):.0f}%)  "
          f"片段数 {len(cuts)} -> {len(dedup)}")

    op = a.out or os.path.splitext(a.cuts)[0] + f".{mode}.json"
    with open(op, "w", encoding="utf-8") as f:
        json.dump({"mode": mode, "max_gap": max_gap, "hold": a.hold,
                   "tier": a.tier, "min_seg": min_seg,
                   "cuts": dedup}, f, ensure_ascii=False, indent=1)
    print(f"-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
