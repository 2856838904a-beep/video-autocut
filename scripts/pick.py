"""
pick.py —— 统一选段器(品类通过 --genre 切换,不再每个品类一个脚本)

替代了早期的 pick_events.py / pick_events_survival.py —— 那两个把品类差异
写死在代码里,加品类就要复制脚本。现在品类词典在 genres.py,加一行就多一个品类。

核心逻辑(与品类无关,已实测):
  1. 用「停顿」切出完整对话事件 —— 阈值从数据 p93 推,不拍脑袋定
  2. 整个事件作为候选单元(不是"单句爆点 + 时间窗口",那会剁碎对话)
  3. 事件过长时,只在事件内部的呼吸口(>= split_gap)细分,绝不切在句子中间
  4. 按品类词典打分,首尾留呼吸余量
  5. 同时输出「未入选的完整事件」供人核对,避免漏掉好内容

用法:
  python pick.py --transcript t.json --metrics m.json --genre survival [--list]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import numpy as np
except ImportError:
    np = None

import genres
import tiers


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 60:02d}:{t % 60:02d}"


def build_matcher(dims: dict):
    compiled = {k: (w, [re.compile(p) for p in pats]) for k, (w, pats) in dims.items()}
    fill = genres.FILL

    def score_text(t: str) -> dict:
        out = {}
        for name, (w, pats) in compiled.items():
            if name == "表达密度":
                continue                      # 占位维度,由字数密度单独算
            hits = sum(1 for p in pats if p.search(t))
            if hits:
                out[name] = round(hits * w, 2)
        if fill.match(t.strip()):
            out = {k: v - 3.0 for k, v in out.items()}
        return out

    return score_text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript")
    ap.add_argument("--metrics")
    ap.add_argument("--genre", default="survival")
    ap.add_argument("--tier", default=None,
                    choices=["short", "medium", "long", "auto", "none"],
                    help="成片形态:short 短视频切片 / medium 中视频 / long 长视频 / "
                         "auto 按素材总长自动选 / none 沿用品类默认(不给就是 none)。"
                         "显式参数(如 --max-unit)仍然优先于档位。")
    ap.add_argument("--list", action="store_true", help="列出所有品类词典后退出")
    ap.add_argument("--list-tiers", action="store_true", help="列出成片分档后退出")
    ap.add_argument("--top", type=int, default=None, help="最多选几段(默认按档位/8)")
    # 以下留空则用品类默认值
    ap.add_argument("--gap", type=float, default=None)
    ap.add_argument("--split-gap", type=float, default=None)
    ap.add_argument("--min", dest="min_dur", type=float, default=None)
    ap.add_argument("--max", dest="max_dur", type=float, default=None)
    ap.add_argument("--budget-ratio", type=float, default=None,
                    help="成片总长占素材比例(默认用品类值,生存类 0.35)")
    ap.add_argument("--budget-sec", type=float, default=None,
                    help="成片总长硬上限(秒)。给了就覆盖 --budget-ratio;"
                         "超预算的单个事件会先按呼吸口切分,保证不超")
    ap.add_argument("--max-unit", type=float, default=None,
                    help="单个片段的时长上限(秒)。超过就逐级降低停顿阈值继续切,"
                         "保证段长有上界(默认取 --max 的 1/3)。"
                         "调小 → 段更碎更均匀;调大 → 保留长对话")
    ap.add_argument("--pad", type=float, default=None,
                    help="首尾呼吸余量(秒)。默认按档位(short 0.25 / medium 0.4 / long 0.5)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    if a.list:
        print("可用品类词典:")
        print(genres.list_profiles())
        return 0

    if a.list_tiers:
        print("成片形态分档:")
        print(tiers.list_tiers())
        return 0

    if not a.transcript or not a.metrics:
        print("需要 --transcript 和 --metrics(或用 --list / --list-tiers)", file=sys.stderr)
        return 2

    prof = genres.get(a.genre)

    tr = json.load(open(a.transcript, encoding="utf-8"))
    segs = tr["segments"]
    mp = json.load(open(a.metrics, encoding="utf-8"))
    wins = mp["windows"]
    win = mp.get("window", 1.0)
    n = len(wins)
    total = mp["duration"]

    # ---- 成片形态分档:显式参数 > --tier > 品类默认 ----
    # 短切片和长片的选段参数是相反的(段短点炸 vs 段长保叙事),
    # 所以档位要压过品类默认;但用户在命令行上明确写了的参数永远优先。
    tier_name = a.tier
    if tier_name == "auto":
        tier_name = tiers.auto(total)
    elif tier_name == "none":
        tier_name = None
    td = tiers.pick_defaults(tier_name)
    tier_note = tiers.get(tier_name) if tier_name else {}

    def resolve(cli_val, tier_key, prof_val):
        if cli_val is not None:
            return cli_val
        if tier_key in td:
            return td[tier_key]
        return prof_val

    gap_thr = a.gap if a.gap is not None else prof["gap"]
    split_gap = a.split_gap if a.split_gap is not None else prof["split_gap"]
    min_dur = resolve(a.min_dur, "min", prof["min"])
    max_dur = resolve(a.max_dur, "max", prof["max"])
    budget_ratio = a.budget_ratio if a.budget_ratio is not None else prof["budget_ratio"]
    motion_div = prof["motion_div"]
    top_n = resolve(a.top, "top", 8)
    pad = resolve(a.pad, "pad", 0.5)
    dens_boost = td.get("density_boost", 1.0)
    vari_boost = td.get("variety_boost", 1.0)
    still_min_run = resolve(None, "still_min_run", prof.get("still_min_run", 8.0))
    still_motion_max = prof.get("still_motion_max", 45.0)
    eff_max_unit = a.max_unit if a.max_unit is not None else td.get("max_unit")

    def motion_at(t: float) -> float:
        i = int(t // win)
        return wins[i]["motion"] if 0 <= i < n else 0.0

    def silent_at(t: float) -> bool:
        i = int(t // win)
        return bool(wins[i]["silent"]) if 0 <= i < n else False

    score_text = build_matcher(prof["dims"])

    mark = "✅ 已验证" if prof.get("verified") else "⚠️  未验证(词典为推导,首次请小样抽检)"
    print(f"品类 {a.genre} — {prof['label']}")
    print(f"状态 {mark}")
    print(f"说明 {prof['note']}")
    if tier_name:
        print(f"档位 {tier_name} — {tier_note['label']}")
        print(f"     {tier_note['why']}")
        print(f"     段长 {min_dur:.0f}~{max_dur:.0f}s   单段上限 "
              f"{eff_max_unit if eff_max_unit else max(min_dur * 2, max_dur / 3.0):.0f}s   "
              f"最多 {top_n} 段")
    else:
        print("档位 未指定(沿用品类默认;用 --tier short|medium|long 按成片形态调参)")
    print()

    # ---- 事件切分 ----
    gaps = [segs[i]["start"] - segs[i - 1]["end"] for i in range(1, len(segs))]
    gs = sorted(gaps)
    if gap_thr > 0:
        gthr, gsrc = gap_thr, "手动"
    else:
        gthr, gsrc = max(4.0, gs[int(len(gs) * 0.93)]), "自动 p93"
    print(f"素材 {fmt(total)}  {len(segs)} 句   事件切分阈值 {gthr:.1f}s ({gsrc})")

    events: list[list[dict]] = []
    cur = [segs[0]]
    for i in range(1, len(segs)):
        if segs[i]["start"] - segs[i - 1]["end"] > gthr:
            events.append(cur)
            cur = [segs[i]]
        else:
            cur.append(segs[i])
    events.append(cur)
    print(f"对话事件 {len(events)} 个   时长区间 {min_dur:.0f}~{max_dur:.0f}s")
    if prof.get("still_hint"):
        print("★ 该品类会额外找「无解说 + 画面平稳」的空镜区间")
    print()

    # ---- 长事件细分 ----
    units: list[list[dict]] = []
    for ev in events:
        if ev[-1]["end"] - ev[0]["start"] <= max_dur:
            units.append(ev)
            continue
        chunk = [ev[0]]
        for i in range(1, len(ev)):
            gap = ev[i]["start"] - ev[i - 1]["end"]
            cur_dur = ev[i - 1]["end"] - chunk[0]["start"]
            if gap >= split_gap and cur_dur >= min_dur:
                units.append(chunk)
                chunk = [ev[i]]
            else:
                chunk.append(ev[i])
        if chunk:
            units.append(chunk)

    def score_unit(u: list[dict]) -> dict:
        dims: dict[str, float] = {}
        for s in u:
            for k, v in score_text(s["text"]).items():
                dims[k] = round(dims.get(k, 0.0) + v, 2)
        t0, t1 = u[0]["start"], u[-1]["end"]
        d = max(1.0, t1 - t0)
        chars = sum(len(s["text"]) for s in u)
        ms = [motion_at(t0 + k * 0.5) for k in range(max(1, int(d * 2)))]
        peak_m = max(ms) if ms else 0.0
        avg_m = sum(ms) / len(ms) if ms else 0.0
        sil_ratio = (sum(1 for k in range(max(1, int(d)))
                         if silent_at(t0 + k + 0.5)) / max(1, int(d)))
        # ★ 正负分开算:pos 用于"价值量",penalty(≤0)必须真的从总分里扣。
        #   早期版本只累加 v>0,导致「失误口误 -3.5」「填充词 -3.0」这类
        #   负向维度**完全失效** —— talk 品类的"去废"逻辑等于没写。
        pos = sum(v for v in dims.values() if v > 0)
        net = sum(dims.values())
        penalty = round(net - pos, 2)
        wpm = chars / (d / 60.0)
        density = pos / max(1.0, d / 30.0)
        variety = len([k for k, v in dims.items() if v > 0])
        score = (density * 0.6 * dens_boost + pos * 0.08
                 + variety * 1.2 * vari_boost + peak_m / motion_div + penalty)
        if a.genre == "talk":
            score += min(wpm, 300) / 30.0        # 口播类看重表达密度
        if a.genre == "vlog":
            score += sil_ratio * 3.0             # 空镜偏好"少解说"
        return {
            "start": round(t0, 1), "end": round(t1, 1), "dur": round(d, 1),
            "sentences": len(u), "chars": chars, "wpm": round(wpm, 1),
            "dims": dims, "raw": round(pos, 1), "penalty": penalty,
            "variety": variety,
            "peak_motion": round(peak_m, 1), "avg_motion": round(avg_m, 1),
            "silence_ratio": round(sil_ratio, 2),
            "score": round(score, 2),
            "text": " ".join(s["text"] for s in u),
        }

    # ---- 长事件细分:逐级降低停顿阈值,直到每段都 <= max_unit ----
    if a.budget_sec is not None:
        budget, budget_src = a.budget_sec, "命令行硬上限"
    elif a.budget_ratio is not None:
        budget, budget_src = max(200.0, total * a.budget_ratio), f"命令行比例 {a.budget_ratio:.0%}"
    elif tier_name:
        budget, budget_src = tiers.budget_for(tier_name, total, budget_ratio)
    else:
        budget, budget_src = max(200.0, total * budget_ratio), f"品类比例 {budget_ratio:.0%}"
    print(f"预算 {budget / 60:.1f} 分钟({budget_src})")
    # 为什么需要逐级降阈值:密集对话的"事件"可能长达 8 分钟,只按 split_gap
    # 切不动,结果一个场次只有 2 段、每段 8 分钟,观感和别的场次差很多。
    # 逐级降阈值 = 在更小的呼吸口切,保证段长有上界,且始终切在停顿处。
    def split_pass(units_in: list[list[dict]], gap_thr_local: float,
                   target: float) -> tuple[list[list[dict]], int]:
        out_units, n_split = [], 0
        for u in units_in:
            d = u[-1]["end"] - u[0]["start"]
            if d <= target:
                out_units.append(u)
                continue
            chunk = [u[0]]
            for i in range(1, len(u)):
                gap = u[i]["start"] - u[i - 1]["end"]
                cur = u[i - 1]["end"] - chunk[0]["start"]
                if gap >= gap_thr_local and cur >= min_dur:
                    out_units.append(chunk)
                    chunk = [u[i]]
                    n_split += 1
                else:
                    chunk.append(u[i])
            if chunk:
                out_units.append(chunk)
        return out_units, n_split

    units, _ = split_pass(units, split_gap, max_dur)
    target_unit = (eff_max_unit if eff_max_unit
                   else max(min_dur * 2, max_dur / 3.0))
    for thr in (split_gap, split_gap * 0.6, split_gap * 0.35, 0.8):
        longest = max((u[-1]["end"] - u[0]["start"] for u in units), default=0)
        if longest <= target_unit:
            break
        units, ns = split_pass(units, max(thr, 0.5), max(target_unit, min_dur))
        if ns == 0:
            break

    # 兜底:段内几乎没有停顿的素材(如稀疏解说),降阈值也切不动。
    # 这时只能按时长强制切 —— 但切点选「段内最接近目标的那个停顿」,
    # 尽量落在两句之间,而不是随手指一个位置。
    def force_split(u: list[dict], target: float) -> list[list[dict]]:
        out, cur = [], [u[0]]
        for i in range(1, len(u)):
            cur_d = u[i - 1]["end"] - cur[0]["start"]
            if cur_d < target:
                cur.append(u[i])
                continue
            # 在 [当前起点+target*0.7, 当前末尾+target*0.3] 里找最大停顿
            lo = u[i - 1]["end"] - target * 0.3
            best_k, best_gap = i, -1.0
            for k in range(1, len(cur)):
                g = cur[k]["start"] - cur[k - 1]["end"]
                if g > best_gap:
                    best_gap, best_k = g, k
            if best_gap >= 0 and best_k > 0:
                out.append(cur[:best_k])
                cur = cur[best_k:]
            else:
                out.append(cur)
                cur = [u[i]]
        if cur:
            out.append(cur)
        return out

    if eff_max_unit:
        forced: list[list[dict]] = []
        for u in units:
            if u[-1]["end"] - u[0]["start"] <= eff_max_unit * 1.05:
                forced.append(u)
            else:
                forced.extend(force_split(u, eff_max_unit))
        if len(forced) != len(units):
            print(f"强制切分(段内无停顿):{len(units)} -> {len(forced)}")
        units = forced

    # 超预算的单元再切(预算通常是比 target 更紧的约束)
    capped: list[list[dict]] = []
    for u in units:
        d = u[-1]["end"] - u[0]["start"]
        if d <= budget:
            capped.append(u)
            continue
        chunk = [u[0]]
        for i in range(1, len(u)):
            gap = u[i]["start"] - u[i - 1]["end"]
            cur = u[i - 1]["end"] - chunk[0]["start"]
            if gap >= 0.8 and cur >= min_dur:
                capped.append(chunk)
                chunk = [u[i]]
            else:
                chunk.append(u[i])
        if chunk:
            capped.append(chunk)
    if len(capped) != len(units):
        print(f"超预算单元已切分:{len(units)} -> {len(capped)}")
    units = capped

    lens = sorted((u[-1]["end"] - u[0]["start"]) for u in units)
    if lens:
        print(f"候选单元 {len(units)} 个,时长 min {lens[0]:.0f}s / "
              f"中位 {lens[len(lens) // 2]:.0f}s / max {lens[-1]:.0f}s")

    scored = [score_unit(u) for u in units]
    scored.sort(key=lambda x: -x["score"])

    # 两轮装填:先用"能完整装下"的段贪心填满预算;若还剩较多预算(>20%),
    # 再放宽到"按剩余空间裁剪"的段 —— 否则遇到段长普遍偏大的素材,会只选出
    # 很少几段、预算大量空置(实测某文件预算 15.7 分钟只选出 6.5 分钟)。
    def fill(allow_trim: bool):
        pk, us = [], 0.0
        for s in scored:
            if s["dur"] < min_dur or not s["dims"]:
                continue
            # 去废:负向维度扣到总分不剩,或口误/卡壳扣分很重 -> 直接不选。
            # (talk 品类的核心诉求就是"删掉卡壳重来",不能靠分数低自然淘汰)
            if s["score"] <= 0 or s.get("penalty", 0.0) <= -6.0:
                continue
            need = s["dur"] + pad * 2
            left = budget - us
            if need <= left:
                pk.append(dict(s))
                us += need
            elif allow_trim and left > max(min_dur + pad * 2, 20.0):
                t = dict(s)
                t["end"] = round(t["start"] + max(min_dur, left - pad * 2), 1)
                t["dur"] = round(t["end"] - t["start"], 1)
                t["trimmed"] = True
                pk.append(t)
                us = budget
            if len(pk) >= top_n or us >= budget - 1:
                break
        return pk, us

    pick, used = fill(allow_trim=False)
    if used < budget * 0.8:
        pick2, used2 = fill(allow_trim=True)
        if used2 > used:
            print(f"预算空置过多({used / 60:.1f}/{budget / 60:.1f} 分),"
                  f"放宽为按剩余空间裁剪 -> {used2 / 60:.1f} 分")
            pick, used = pick2, used2
    # 兜底:去废过滤可能把所有候选都判死(口播卡壳多的素材),此时不能静默出 0 段。
    # 退化成"按分数取头几个",并明确告知用户去废过滤生效了。
    if not pick:
        cands = [s for s in scored if s["dims"]]
        if cands:
            pick = [dict(s) for s in cands[:max(1, min(top_n, 3))]]
            print("⚠️  去废过滤把全部候选判死(负向维度占比过高),"
                  f"已退化为按分数取前 {len(pick)} 段 —— 请人工核对是否素材本身口误密集")
    pick.sort(key=lambda x: x["start"])
    for i, s in enumerate(pick):
        s["start"] = round(max(0.0, s["start"] - pad), 1)
        s["end"] = round(min(total, s["end"] + pad), 1)
        s["dur"] = round(s["end"] - s["start"], 1)
        if i > 0 and s["start"] < pick[i - 1]["end"]:
            s["start"] = pick[i - 1]["end"]
            s["dur"] = round(s["end"] - s["start"], 1)

    # ---- 空镜候选(仅 vlog 品类) ----
    still = []
    if prof.get("still_hint"):
        spoken = [(s["start"], s["end"]) for s in segs]

        def is_speech(t: float) -> bool:
            return any(t0 - 0.3 <= t <= t1 + 0.3 for t0, t1 in spoken)

        run_a = None
        for i in range(n + 1):
            ok = (i < n and not is_speech(i * win + win / 2)
                  and wins[i]["motion"] < still_motion_max and not wins[i]["silent"])
            if ok and run_a is None:
                run_a = i
            elif not ok and run_a is not None:
                if (i - run_a) * win >= still_min_run:
                    still.append({"start": round(run_a * win, 1),
                                  "end": round(i * win, 1),
                                  "dur": round((i - run_a) * win, 1),
                                  "avg_motion": round(sum(wins[j]["motion"]
                                                          for j in range(run_a, i)) / (i - run_a), 1)})
                run_a = None
        still.sort(key=lambda x: -x["dur"])

    # ---- 报告 ----
    tot_pick = sum(s["dur"] for s in pick)
    print("=" * 84)
    print(f"【选段】{len(pick)} 段  共 {tot_pick:.0f}s ({tot_pick / 60:.1f} 分钟)"
          f"  占素材 {tot_pick / total * 100:.0f}%")
    print("=" * 84)
    for i, s in enumerate(pick, 1):
        dt = " ".join(f"{k}{v:.0f}" for k, v in
                      sorted(s["dims"].items(), key=lambda x: -x[1]) if v > 0)
        print(f"\n#{i:>2}  {fmt(s['start'])}–{fmt(s['end'])}  {s['dur']:6.1f}s  "
              f"{s['sentences']:>2}句  {s['chars']:>4}字  {s['wpm']:5.1f}字/分  "
              f"运动峰 {s['peak_motion']:5.1f}  静音 {s['silence_ratio'] * 100:3.0f}%")
        print(f"      维度: {dt or '无'}"
              + (f"   扣分 {s['penalty']:.1f}" if s.get("penalty", 0) < 0 else ""))
        for sg in segs:
            if sg["end"] > s["start"] and sg["start"] < s["end"]:
                print(f"      [{fmt(sg['start'])}] {sg['text']}")

    if still:
        print("\n" + "=" * 84)
        print("【空镜候选】无解说 + 画面平稳")
        print("=" * 84)
        for s in still[:10]:
            print(f"   {fmt(s['start'])}–{fmt(s['end'])}  {s['dur']:6.1f}s  "
                  f"平均运动 {s['avg_motion']:5.1f}")

    print("\n" + "=" * 84)
    print("【未入选的完整事件】按价值量排序 —— 请核对有没有漏掉好内容")
    print("=" * 84)
    spans = [(s["start"], s["end"]) for s in pick]
    rest = []
    for ev in events:
        e0, e1 = ev[0]["start"], ev[-1]["end"]
        if e1 - e0 < 5:
            continue
        if any(not (e1 <= p0 or e0 >= p1) for p0, p1 in spans):
            continue
        rest.append(score_unit(ev))
    rest.sort(key=lambda x: -x["score"])
    for s in rest:
        dt = " ".join(f"{k}{v:.0f}" for k, v in
                      sorted(s["dims"].items(), key=lambda x: -x[1]) if v > 0) or "无"
        print(f"  {fmt(s['start'])}–{fmt(s['end'])}  {s['dur']:6.1f}s  "
              f"价值 {s['raw']:5.1f}  {dt:<30} | {s['text'][:42]}")

    op = a.out or os.path.splitext(a.transcript)[0] + f".{a.genre}.cuts.json"
    with open(op, "w", encoding="utf-8") as f:
        json.dump({"genre": a.genre,
                   "tier": tier_name,
                   "duration": total, "gap_threshold": gthr,
                   "budget_sec": round(budget, 1),
                   "params": {"min_dur": min_dur, "max_dur": max_dur,
                              "max_unit": eff_max_unit, "top": top_n, "pad": pad,
                              "density_boost": dens_boost,
                              "variety_boost": vari_boost},
                   "profile": {k: v for k, v in prof.items() if k != "dims"},
                   "cuts": [{"start": s["start"], "end": s["end"], "dur": s["dur"],
                             "score": s["score"], "dims": s["dims"],
                             "penalty": s.get("penalty", 0.0),
                             "text": s["text"]} for s in pick],
                   "still": still,
                   "all_units": scored}, f, ensure_ascii=False, indent=1)
    print(f"\n-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
