"""
continuous.py —— 把「碎片化选段」重建成「连续块」(解决观感"剪得碎、不连贯")

★ 为什么需要这一层(2026-11 影之刃实测反馈:"剪的太碎了，连续性有问题"):

  上游 pick.py 按"价值峰值"选段,得到的是大量 6~30 秒的小段;tighten.py 又会
  把它们按句子切成更碎的片段。实测一集 13 分钟里有 **96 个片段、中位段长 6.8 秒、
  53 处超过 1 分钟的跳变(最长跳 39.8 分钟)** —— 观感就是"跳剪合集",
  完全不是"能连着看"的东西。

  连续性不是靠"少选几个点"解决的,而是要让每段**变成一段连续时间**:

    1. 展开:每个选段向两侧扩到 --min-block(默认 75 秒),扩出来的部分
       是这个场景的上下文(观众要看的是"这一段发生了什么",不是孤立的一击)
    2. 吸附:扩边不要切在动作/台词中间 —— 在 ±--snap 秒内找最近的「安静点」
       (静音或低运动)作为切点
    3. 合并:展开后重叠、或间隔 <= --merge-gap 的块直接连成一块
       (连通 = 中间的过渡也保留,而不是跳过去)
    4. 控预算:块变长了,总时长会涨;按块价值从低到高裁掉,直到回到预算内

用法:
  python continuous.py --cuts a.cuts.json --metrics a.metrics.json \
         --min-block 75 --max-block 180 --merge-gap 15 --budget-sec 6600 \
         --out a.continuous.cuts.json
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


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 60:02d}:{t % 60:02d}"


def load_cuts(path: str) -> tuple[list[dict], str]:
    d = json.load(open(path, encoding="utf-8"))
    cuts = d.get("cuts", d if isinstance(d, list) else [])
    src = (d.get("src") or d.get("file") or os.path.basename(path)
           .replace(".cuts.json", "").replace(".json", ""))
    out = []
    for c in cuts:
        out.append({"src": c.get("src") or src,
                    "start": float(c["start"]), "end": float(c["end"]),
                    "dur": float(c.get("dur") or (c["end"] - c["start"])),
                    "score": float(c.get("score") or 0.0),
                    "text": c.get("text", "")})
    return out, src


def quiet_points(wins: list[dict], win: float) -> tuple[list[int], list[int]]:
    """返回 (静音秒索引, 低运动秒索引),用于把切点吸到"不突兀"的位置。"""
    silent = [i for i, w in enumerate(wins) if w.get("silent")]
    mo = [w.get("motion", 0.0) for w in wins]
    if not mo:
        return silent, []
    mo_sorted = sorted(mo)
    p40 = mo_sorted[int(len(mo_sorted) * 0.40)]
    quiet = [i for i, m in enumerate(mo) if m <= p40]
    return silent, quiet


def snap(t: float, cand: list[int], total: float, snap_win: float) -> float:
    """把时间 t 吸到 snap_win 内最近的候选秒;找不到就原样返回。"""
    if not cand:
        return t
    best, bestd = t, 1e9
    lo, hi = t - snap_win, t + snap_win
    for i in cand:
        d = abs(i - t)
        if lo <= i <= hi and d < bestd:
            best, bestd = float(i), d
    return max(0.0, min(total, best))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuts", action="append", required=True)
    ap.add_argument("--metrics", action="append", default=[],
                    help="与 --cuts 一一对应的 metrics.json(用于找安静切点)")
    ap.add_argument("--min-block", type=float, default=75.0)
    ap.add_argument("--max-block", type=float, default=180.0)
    ap.add_argument("--merge-gap", type=float, default=15.0,
                    help="两块间隔 <= 此值时连成一块(而不是跳过)")
    ap.add_argument("--snap", type=float, default=8.0, help="切点吸附搜索半径(秒)")
    ap.add_argument("--budget-sec", type=float, default=0.0,
                    help="总时长上限;超过时按块价值裁掉(0=不限)")
    ap.add_argument("--seed-top", type=int, default=0,
                    help="先按分数取前 N 个选段当「场景中心」再展开(0=全部展开)。"
                         "为什么需要:把 300 个 8 秒碎片全展开成 75 秒再裁掉大半纯属浪费,"
                         "正确做法是先少选种子,再让每颗种子长成一段连续场景。")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    met_map = {os.path.basename(m): m for m in a.metrics}

    # ---- 0) 全局精选种子:先少选,再展开成连续场景 ----
    if a.seed_top and a.seed_top > 0:
        all_cuts = []
        for cf in a.cuts:
            cs, src = load_cuts(cf)
            for c in cs:
                c["_file"] = cf
                all_cuts.append(c)
        all_cuts.sort(key=lambda c: -c["score"])
        keep_keys = {(c["_file"], round(c["start"], 2), round(c["end"], 2))
                     for c in all_cuts[:a.seed_top]}
        kept_files = {}
        for c in all_cuts:
            key = (c["_file"], round(c["start"], 2), round(c["end"], 2))
            if key in keep_keys:
                kept_files.setdefault(c["_file"], set()).add(key[1:])
        print(f"种子筛选: {len(all_cuts)} 个选段 -> 保留分数最高的 {a.seed_top} 个")
        a.cuts = list(kept_files.keys())
        seed_keep = kept_files
    else:
        seed_keep = None

    blocks: list[dict] = []
    per_file_stats = []

    for cf in a.cuts:
        cuts, src = load_cuts(cf)
        if seed_keep is not None:
            keys = seed_keep.get(cf, set())
            cuts = [c for c in cuts if (round(c["start"], 2), round(c["end"], 2)) in keys]
        base = os.path.basename(cf).replace(".cuts.json", "")
        mpath = met_map.get(base + ".metrics.json") or met_map.get(base + ".json")
        if not mpath:
            for k, v in met_map.items():
                if k.startswith(base):
                    mpath = v
                    break
        wins, win, total, silent, quiet = [], 1.0, 0.0, [], []
        if mpath and os.path.exists(mpath):
            mp = json.load(open(mpath, encoding="utf-8"))
            wins = mp["windows"]
            win = mp.get("window", 1.0)
            total = float(mp["duration"])
            silent, quiet = quiet_points(wins, win)
        if not cuts:
            continue
        cuts.sort(key=lambda c: c["start"])
        n_before = len(cuts)
        avg_before = sum(c["dur"] for c in cuts) / n_before

        # ---- 1) 展开:每段补足到 min-block(向两边对称),夹在相邻段之间 ----
        expanded = []
        for i, c in enumerate(cuts):
            t0, t1 = c["start"], c["end"]
            need = a.min_block - (t1 - t0)
            if need > 0:
                # 左右各分一半,但受邻居限制(邻居之间留 2 秒)
                lo_lim = expanded[-1]["end"] + 2.0 if expanded else 0.0
                hi_lim = cuts[i + 1]["start"] - 2.0 if i + 1 < len(cuts) else total
                t0 = max(lo_lim, t0 - need / 2)
                t1 = min(hi_lim, t1 + need / 2)
                # 若一边被夹住,再向另一边补足
                if t1 - t0 < a.min_block:
                    t1 = min(hi_lim, t0 + a.min_block)
                if t1 - t0 < a.min_block:
                    t0 = max(lo_lim, t1 - a.min_block)
            # 吸附切点到安静处
            t0 = snap(t0, silent or quiet, total, a.snap)
            t1 = snap(t1, silent or quiet, total, a.snap)
            if t1 - t0 < 2.0:
                continue
            expanded.append({"src": src, "start": t0, "end": t1,
                             "score": c["score"], "text": c.get("text", ""),
                             "n_src_cuts": 1})

        # ---- 2) 合并:重叠或间隔 <= merge-gap 的连成一段 ----
        merged: list[dict] = []
        for c in expanded:
            if merged and c["start"] - merged[-1]["end"] <= a.merge_gap:
                m = merged[-1]
                m["end"] = max(m["end"], c["end"])
                m["score"] = max(m["score"], c["score"])
                m["n_src_cuts"] += c["n_src_cuts"]
                m["text"] = (m["text"] + " " + c["text"]).strip()
            else:
                merged.append(dict(c))

        # ---- 3) 超长块截断到 max-block(保留分值中心) ----
        capped = []
        for m in merged:
            if m["end"] - m["start"] > a.max_block:
                mid = (m["start"] + m["end"]) / 2
                m["start"] = max(0.0, mid - a.max_block / 2)
                m["end"] = m["start"] + a.max_block
            capped.append(m)

        for m in capped:
            m["dur"] = round(m["end"] - m["start"], 1)
            m["start"] = round(m["start"], 1)
            m["end"] = round(m["end"], 1)
        blocks.extend(capped)
        per_file_stats.append({
            "src": src, "cuts": n_before, "avg_cut": round(avg_before, 1),
            "blocks": len(capped),
            "avg_block": round(sum(m["dur"] for m in capped) / max(1, len(capped)), 1),
            "selected_min": round(sum(c["dur"] for c in cuts) / 60, 1),
            "block_min": round(sum(m["dur"] for m in capped) / 60, 1),
        })
        print(f"{src:<26} 选段 {n_before:>3} (均 {avg_before:4.1f}s) -> "
              f"连续块 {len(capped):>3} (均 {per_file_stats[-1]['avg_block']:5.1f}s)  "
              f"{per_file_stats[-1]['selected_min']:5.1f} 分 -> {per_file_stats[-1]['block_min']:5.1f} 分")

    # ---- 4) 控预算:按「块价值」从低到高裁 ----
    total_sec = sum(m["dur"] for m in blocks)
    dropped = 0
    if a.budget_sec and total_sec > a.budget_sec:
        # 价值 = 原始选段分值总和 / 块时长(按时长归一,避免长块霸占)
        for m in blocks:
            m["value"] = (m["score"] + 1.0) * m["n_src_cuts"] / max(1.0, m["dur"] / 60.0)
        blocks.sort(key=lambda m: m["value"])
        while blocks and sum(m["dur"] for m in blocks) > a.budget_sec:
            blocks.pop(0)
            dropped += 1
        total_sec = sum(m["dur"] for m in blocks)

    blocks.sort(key=lambda m: (m["src"], m["start"]))
    total_sec = sum(m["dur"] for m in blocks)
    print(f"\n合计 {len(blocks)} 个连续块 / {total_sec / 60:.1f} 分钟"
          + (f"(超预算裁掉 {dropped} 块)" if dropped else ""))

    op = a.out
    if not op:
        op = os.path.splitext(a.cuts[0])[0] + ".continuous.cuts.json"
    with open(op, "w", encoding="utf-8") as f:
        json.dump({"mode": "continuous",
                   "params": {"min_block": a.min_block, "max_block": a.max_block,
                              "merge_gap": a.merge_gap, "snap": a.snap,
                              "budget_sec": a.budget_sec},
                   "stats": per_file_stats,
                   "cuts": blocks}, f, ensure_ascii=False, indent=1)
    print(f"-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
