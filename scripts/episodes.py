"""
episodes.py —— 把选段切成一集一集的「连续剧」

为什么需要单独一层:
  pick.py 管"选哪些段",不管"这些段怎么分集"。而长素材(几十小时)选出来的
  总时长往往是一集的好几倍 —— 直接塞进一个草稿就是 40 分钟的缝合怪。
  实际交付形态是:**每集 10~15 分钟,各自一个剪映草稿,串起来看是连续剧。**

分集原则(为什么不是简单按秒数硬切):
  1. **在自然断点切**:优先在「同一素材内的长停顿」「换文件(换场次)」处断开,
     不要在一段对话/一场 BOSS 战中间断。硬按秒数切会切出"话说一半"的集尾。
  2. **集长是区间不是定值**:--target-min 13 --tolerance-min 3 表示 10~16 分钟都可接受,
     在此区间内找最自然的断点。用户说"时长不用定太死"就是这条。
  3. **每集内部保持时间顺序**:观众要看的是"这一晚的精彩",不是打乱重排。
  4. **换场次允许跨文件**:一集可以包含多个录像文件的片段(文件名的日期时间即顺序)。

用法:
  python episodes.py --cuts a.cuts.json --cuts b.cuts.json \
         --target-min 13 --tolerance-min 3 --out-dir out/episodes

  # 只看分集计划,不写文件
  python episodes.py --cuts ... --dry

  # 单文件(不看跨文件顺序)
  python episodes.py --cuts work/xxx.cuts.json --target-min 13
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


def load_cuts(path: str) -> list[dict]:
    d = json.load(open(path, encoding="utf-8"))
    cuts = d.get("cuts", d if isinstance(d, list) else [])
    src = (d.get("src") or d.get("file") or os.path.basename(path)
           .replace(".cuts.json", "").replace(".json", ""))
    out = []
    for c in cuts:
        out.append({"src": c.get("src") or src,
                    "start": float(c["start"]), "end": float(c["end"]),
                    "dur": float(c.get("dur") or (c["end"] - c["start"])),
                    "score": c.get("score", 0.0),
                    "text": c.get("text", "")})
    return out


def pack(cuts: list[dict], target: float, tol: float,
         soft_min: float, gap_split: float) -> list[list[dict]]:
    """按区间 + 自然断点装箱。返回每集的 cut 列表(集内保持时间顺序)。

    cuts 需已按「全局播放顺序」排好(调用方保证:文件顺序 + 文件内时间)。
    """
    lower, upper = target - tol, target + tol
    eps: list[list[dict]] = []
    i, n = 0, len(cuts)
    while i < n:
        cur, total = [], 0.0
        while i < n:
            c = cuts[i]
            # 这一集已经够软下限,且下一段前有长停顿 / 换了文件 -> 自然断点,收
            if cur and total >= lower:
                prev = cur[-1]
                new_src = c["src"] != prev["src"]
                gap = (c["start"] - prev["end"]) if not new_src else 9e9
                if gap >= gap_split:
                    break
            if total + c["dur"] > upper and cur:
                break
            cur.append(c)
            total += c["dur"]
            i += 1
            if total >= upper:
                break
        if not cur:                     # 单段就超上限 -> 独占一集
            cur = [cuts[i]]
            i += 1
        eps.append(cur)

    # 后处理:小于 soft_min 的尾集,必须并进上一集 —— 宁可某集略长,也不要 2 分钟的残集。
    # 允许超到 upper + soft_min(自测发现:9.0 + 2.25 只超上限 0.25 分钟,
    # 若严格卡 upper 就会留个 2 分钟的"第 3 集",观感上不可接受)。
    if len(eps) >= 2:
        while len(eps) >= 2:
            tail = eps[-1]
            tail_dur = sum(c["dur"] for c in tail)
            if tail_dur >= soft_min:
                break
            prev_dur = sum(c["dur"] for c in eps[-2])
            if prev_dur + tail_dur > upper + soft_min:
                break
            eps[-2] = eps[-2] + tail
            eps.pop()
    return eps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuts", action="append", required=True,
                    help="可多次给:每个素材一个 cuts.json(顺序即播放顺序)")
    ap.add_argument("--target-min", type=float, default=13.0)
    ap.add_argument("--tolerance-min", type=float, default=3.0)
    ap.add_argument("--min-min", type=float, default=6.0,
                    help="一集的下限;尾集不足此值会尝试并入上一集")
    ap.add_argument("--gap-split", type=float, default=15.0,
                    help="同一素材内,段间停顿 >= 此值视为自然断点(秒)")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--dry", action="store_true", help="只打印分集计划")
    a = ap.parse_args()

    cuts: list[dict] = []
    for p in a.cuts:
        cs = load_cuts(p)
        print(f"  载入 {os.path.basename(p):<44} {len(cs):>3} 段 "
              f"{sum(c['dur'] for c in cs):>7.1f}s")
        cuts.extend(cs)
    cuts.sort(key=lambda c: (c["src"], c["start"]))
    total = sum(c["dur"] for c in cuts)
    print(f"\n合计 {len(cuts)} 段 / {total / 60:.1f} 分钟   "
          f"目标 {a.target_min:.0f}±{a.tolerance_min:.0f} 分钟/集")

    eps = pack(cuts, a.target_min * 60, a.tolerance_min * 60,
               a.min_min * 60, a.gap_split)
    print(f"-> {len(eps)} 集\n")
    print(f"{'集':>3}  {'时长':>8}  {'段数':>4}  {'占比':>6}  时间范围(按素材顺序)")
    print("-" * 78)
    for i, ep in enumerate(eps, 1):
        d = sum(c["dur"] for c in ep)
        first, last = ep[0], ep[-1]
        srcs = sorted({c["src"] for c in ep})
        span = (f"{os.path.basename(first['src'])} {fmt(first['start'])}"
                f" → {os.path.basename(last['src'])} {fmt(last['end'])}")
        print(f"{i:>3}  {d / 60:6.1f}分  {len(ep):>4}  {d / total * 100:5.1f}%  "
              f"{span}" + (f"   [{len(srcs)} 个素材]" if len(srcs) > 1 else ""))

    if not a.dry:
        out_dir = a.out_dir or os.path.dirname(os.path.abspath(a.cuts[0]))
        os.makedirs(out_dir, exist_ok=True)
        manifest = []
        for i, ep in enumerate(eps, 1):
            name = f"ep{i:02d}"
            p = os.path.join(out_dir, name + ".cuts.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"episode": i, "target_min": a.target_min,
                           "duration": round(sum(c["dur"] for c in ep), 2),
                           "cuts": ep}, f, ensure_ascii=False, indent=1)
            manifest.append({"episode": i, "file": p,
                              "duration_min": round(sum(c["dur"] for c in ep) / 60, 2),
                              "cuts": len(ep),
                              "srcs": sorted({c["src"] for c in ep})})
            print(f"  -> {p}")
        mp = os.path.join(out_dir, "episodes.json")
        with open(mp, "w", encoding="utf-8") as f:
            json.dump({"target_min": a.target_min, "total_min": round(total / 60, 2),
                       "episodes": manifest}, f, ensure_ascii=False, indent=1)
        print(f"  -> {mp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
