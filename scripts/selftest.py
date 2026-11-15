"""
selftest.py —— 无需任何真实素材的端到端自检

为什么需要它:
  这条流水线以前只能靠"拿真素材跑一遍"来验证,换台机器/换个账户时根本没法确认
  改动有没有把选段逻辑弄坏。本脚本用**合成文稿 + 合成画面指标**把可判定的部分
  变成断言,几秒钟跑完:

  1. 三档成片形态(short/medium/long)确实产出不同预算与段数,且预算单调递增
  2. 分档参数确实压过了品类默认(--tier 生效)
  3. 显式参数仍然优先于档位(命令行 > --tier)
  4. 负向维度(口误/填充词)真的会扣分,不再被 `if v > 0` 丢弃
  5. tighten 三档压缩率单调:short 压得最狠,long 最接近原样
  6. rdr_compose 三档预算不同(过场优先逻辑仍成立)

用法:
  python selftest.py            # 跑全部检查
  python selftest.py --keep     # 保留临时目录以便人工看输出

退出码 0 = 全过;非 0 = 有断言失败(错误信息会指出是哪一项)。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print(f"  ✅ {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL.append(f"{name}: {detail}")
        print(f"  ❌ {name}  {detail}")


def run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """跑子脚本;失败时把 stdout/stderr 一并抛出,免得只看到一句 'returned 1'。"""
    p = subprocess.run([PY] + args, cwd=cwd or HERE,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"{args} 失败(exit {p.returncode})\n"
                           f"--- stdout ---\n{p.stdout[-3000:]}\n"
                           f"--- stderr ---\n{p.stderr[-2000:]}")
    return p


# ============================================================ 合成素材
def make_transcript(path: str, total: float, lines: list[str],
                    step: float = 4.0, event_gap: float = 8.0,
                    gap_every: int = 9) -> dict:
    """造一份文稿:每 step 秒一句,每 gap_every 句后插一个 event_gap 的停顿。

    event_gap 要大于 pick.py 自动算出的 p93 阈值(p93 由数据决定),这样
    事件切分才会真的切出多个"完整对话事件",而不是一个大事件。
    """
    segs, t, i = [], 0.0, 0
    while t < total - 5:
        text = lines[i % len(lines)]
        dur = max(2.5, min(step - 0.5, len(text) * 0.45))
        segs.append({"start": round(t, 2), "end": round(t + dur, 2), "text": text})
        t += dur + 1.8   # 句间空隙 1.8s:真实转写里就是这么稀

        i += 1
        if i % gap_every == 0:
            t += event_gap
    tr = {"language": "zh", "segments": segs}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tr, f, ensure_ascii=False, indent=1)
    return tr


def make_metrics(path: str, total: float, tr: dict, motion_base: float = 30.0) -> dict:
    """造画面指标:1 秒一个窗口。没被文稿覆盖的秒记为静音。"""
    wins, n = [], int(total)
    speech = [(s["start"], s["end"]) for s in tr["segments"]]
    for k in range(n):
        t = k + 0.5
        talking = any(a - 0.2 <= t <= b + 0.2 for a, b in speech)
        # 运动量:基础值 + 每 7 秒一个峰,保证"画面动作"信号非零
        m = motion_base + (25.0 if k % 7 == 0 else 0.0)
        if not talking:
            m *= 0.4
        wins.append({"motion": round(m, 1), "silent": not talking,
                     "loudness": -20.0 if talking else -60.0})
    mp = {"duration": round(total, 2), "window": 1.0, "windows": wins}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mp, f, ensure_ascii=False, indent=1)
    return mp


def load(p: str) -> dict:
    return json.load(open(p, encoding="utf-8"))


# ============================================================ 1~3 分档
def test_tiers(work: str) -> dict:
    print("\n[1] 三档成片形态:预算/段数/段长应逐档放大")
    total = 1200.0                     # 20 分钟素材
    tr = make_transcript(os.path.join(work, "src.json"), total,
                         ["我们先去挖矿吧", "这个钻石够不够", "小心点有僵尸",
                          "看这里地形不错", "我建了一个新基地", "卧槽翻车了"])
    make_metrics(os.path.join(work, "src.metrics.json"), total, tr)

    outs = {}
    for tier in ("short", "medium", "long"):
        op = os.path.join(work, f"t_{tier}.cuts.json")
        run([os.path.join(HERE, "pick.py"),
             "--transcript", os.path.join(work, "src.json"),
             "--metrics", os.path.join(work, "src.metrics.json"),
             "--genre", "survival", "--tier", tier, "--out", op])
        outs[tier] = load(op)

    tot = {k: sum(c["dur"] for c in v["cuts"]) for k, v in outs.items()}
    cnt = {k: len(v["cuts"]) for k, v in outs.items()}
    print(f"     总长 short {tot['short']:.0f}s / medium {tot['medium']:.0f}s / "
          f"long {tot['long']:.0f}s   段数 {cnt['short']}/{cnt['medium']}/{cnt['long']}")

    check("三档预算严格递增",
          tot["short"] < tot["medium"] < tot["long"],
          f"{tot['short']:.0f} < {tot['medium']:.0f} < {tot['long']:.0f}")
    for tier in ("short", "medium", "long"):
        budget = outs[tier]["budget_sec"]
        check(f"{tier} 档不超预算",
              tot[tier] <= budget + 1.0, f"{tot[tier]:.0f}s <= {budget:.0f}s")
    check("short 档段数受限(<=4)",
          cnt["short"] <= 4, f"{cnt['short']} 段")
    check("long 档段数多于 short",
          cnt["long"] > cnt["short"], f"{cnt['long']} > {cnt['short']}")

    # 单段时长上限:short 档不应出现 >20s 的段
    maxdur_s = max((c["dur"] for c in outs["short"]["cuts"]), default=0)
    check("short 档单段 <=20s", maxdur_s <= 20.5, f"最长 {maxdur_s:.1f}s")

    print("\n[2] 档位应压过品类默认(survival 默认 min15/max120)")
    check("short 档段长下限被收到 4s",
          outs["short"]["params"]["min_dur"] == 4.0,
          f"min_dur={outs['short']['params']['min_dur']}")
    check("long 档保留段长上限 120s",
          outs["long"]["params"]["max_dur"] == 120.0,
          f"max_dur={outs['long']['params']['max_dur']}")

    print("\n[3] 显式参数必须优先于档位")
    op = os.path.join(work, "t_explicit.cuts.json")
    run([os.path.join(HERE, "pick.py"),
         "--transcript", os.path.join(work, "src.json"),
         "--metrics", os.path.join(work, "src.metrics.json"),
         "--genre", "survival", "--tier", "short", "--max-unit", "33", "--out", op])
    ex = load(op)
    check("--max-unit 33 覆盖 short 档的 10",
          ex["params"]["max_unit"] == 33.0, f"max_unit={ex['params']['max_unit']}")

    # auto 档:20 分钟素材 -> medium
    op = os.path.join(work, "t_auto.cuts.json")
    run([os.path.join(HERE, "pick.py"),
         "--transcript", os.path.join(work, "src.json"),
         "--metrics", os.path.join(work, "src.metrics.json"),
         "--genre", "survival", "--tier", "auto", "--out", op])
    check("auto 档对 20 分钟素材判为 medium",
          load(op)["tier"] == "medium", f"tier={load(op)['tier']}")
    return outs


# ============================================================ 4 负分生效
def test_penalty(work: str):
    print("\n[4] 负向维度必须真的扣分(talk 品类的『去废』)")
    total = 600.0
    # 6 个事件:3 个干净(结论断言)、3 个带卡壳口误。每句 5 秒。
    # 用 event_gap 把每个事件切开,保证它们各自成为独立候选单元。
    clean = ["所以核心结论是这样的", "关键在于第二步", "记住这一点很重要",
             "总结一下整个过程"]
    dirty = ["呃我重说一下", "不对不对刚才说错了", "这里卡了等一下我看看",
             "那个那个再来一次"]
    lines, t = [], 0.0
    for ev in range(6):
        src = clean if ev % 2 == 0 else dirty
        for _ in range(3):
            text = src[ev % len(src)]
            lines.append((t, t + 4.0, text))
            t += 4.6
        t += 8.0                     # 事件间大停顿
    segs = [{"start": round(a, 2), "end": round(b, 2), "text": c} for a, b, c in lines]
    tr = {"language": "zh", "segments": segs}
    tp = os.path.join(work, "talk.json")
    json.dump(tr, open(tp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    make_metrics(os.path.join(work, "talk.metrics.json"), total, tr)

    op = os.path.join(work, "talk.cuts.json")
    run([os.path.join(HERE, "pick.py"), "--transcript", tp,
         "--metrics", os.path.join(work, "talk.metrics.json"),
         "--genre", "talk", "--tier", "medium", "--out", op])
    d = load(op)

    # all_units 是按分数降序的,取有口误 / 没口误两组的平均名次
    dirty_rank, clean_rank = [], []
    for i, u in enumerate(d["all_units"]):
        txt = u["text"]
        is_dirty = any(k in txt for k in ("呃", "不对", "卡了", "那个那个", "重说"))
        (dirty_rank if is_dirty else clean_rank).append(i)
        if is_dirty:
            check(f"口误单元 penalty<0 (#{i})", u["penalty"] < 0,
                  f"penalty={u['penalty']}")
    avg_d = sum(dirty_rank) / len(dirty_rank) if dirty_rank else -1
    avg_c = sum(clean_rank) / len(clean_rank) if clean_rank else -1
    print(f"     名次均值:带口误 {avg_d:.1f}  干净 {avg_c:.1f}(越小越好)")
    check("口误单元整体排在干净单元之后",
          dirty_rank and clean_rank and avg_d > avg_c, f"{avg_d:.1f} > {avg_c:.1f}")

    # 填充词:"嗯" 单独成句必须被扣分(否则 FILL 正则也是空转)
    ep = os.path.join(work, "fill.json")
    json.dump({"language": "zh", "segments": [
        {"start": 0.0, "end": 1.0, "text": "嗯"},
        {"start": 3.0, "end": 4.0, "text": "啊"},
        {"start": 6.0, "end": 20.0, "text": "所以核心结论是这样做"},
    ]}, open(ep, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    tr2 = load(ep)
    make_metrics(os.path.join(work, "fill.metrics.json"), 40.0, tr2)
    op2 = os.path.join(work, "fill.cuts.json")
    run([os.path.join(HERE, "pick.py"), "--transcript", ep,
         "--metrics", os.path.join(work, "fill.metrics.json"),
         "--genre", "talk", "--tier", "short", "--out", op2])
    units = load(op2)["all_units"]
    fill_units = [u for u in units if u["text"].strip() in ("嗯", "啊", "嗯", "啊 ")]
    if fill_units:
        check("纯填充词单元被判负分",
              all(u["score"] <= 0 or u["penalty"] < 0 for u in fill_units),
              str([u["score"] for u in fill_units]))


# ============================================================ 5 tighten
def max_silent_run(cuts_path: str, metrics_path: str) -> float:
    """片段内部最长连续静音(秒)。

    ★ 这才是压缩档位的正确判据:max_gap 的语义就是"允许留多长的静音"。
      总长不可比 —— short 档切得更碎、min_seg 更小,总长反而可能略多于 medium,
      但片段里的静音一定更短。用总长断言会得出错误结论。
    """
    cuts = load(cuts_path)["cuts"]
    mp = load(metrics_path)
    wins, win = mp["windows"], mp.get("window", 1.0)
    worst = 0.0
    for c in cuts:
        i0, i1 = int(c["start"] // win), int(c["end"] // win)
        run = best = 0
        for i in range(i0, min(i1 + 1, len(wins))):
            if wins[i]["silent"]:
                run += 1
                best = max(best, run)
            else:
                run = 0
        worst = max(worst, best * win)
    return worst


def test_tighten(work: str, tiers_out: dict):
    print("\n[5] tighten 三档:片段内残留静音应 short < medium < long")
    src = os.path.join(work, "t_long.cuts.json")
    mp = os.path.join(work, "src.metrics.json")
    before = sum(c["dur"] for c in tiers_out["long"]["cuts"])
    sil, tot = {}, {}
    for tier in ("short", "medium", "long"):
        op = os.path.join(work, f"tight_{tier}.json")
        run([os.path.join(HERE, "tighten.py"), "--cuts", src,
             "--transcript", os.path.join(work, "src.json"),
             "--metrics", mp, "--tier", tier, "--out", op])
        tot[tier] = sum(c["dur"] for c in load(op)["cuts"])
        sil[tier] = max_silent_run(op, mp)
    print(f"     残留最长静音 short {sil['short']:.1f}s / medium {sil['medium']:.1f}s / "
          f"long {sil['long']:.1f}s   (总长 {tot['short']:.0f}/"
          f"{tot['medium']:.0f}/{tot['long']:.0f}s, 原 {before:.0f}s)")

    check("short 档几乎不留静音(<=1s)", sil["short"] <= 1.0, f"{sil['short']:.1f}s")
    check("long 档保留较长静音(>=1.5s)", sil["long"] >= 1.5, f"{sil['long']:.1f}s")
    check("静音长度随档位单调变长", sil["short"] <= sil["medium"] <= sil["long"],
          f"{sil['short']:.1f} <= {sil['medium']:.1f} <= {sil['long']:.1f}")
    check("三档都真的压缩了(总长不增)", all(v <= before + 1.0 for v in tot.values()),
          f"原 {before:.0f}s")

    # 显式 --max-gap 仍应优先于档位:long 档 + 0.4 必须把静音压到 short 档水平
    op = os.path.join(work, "tight_explicit.json")
    run([os.path.join(HERE, "tighten.py"), "--cuts", src,
         "--transcript", os.path.join(work, "src.json"),
         "--metrics", mp, "--tier", "long", "--max-gap", "0.4", "--out", op])
    sil_exp = max_silent_run(op, mp)
    check("--max-gap 0.4 覆盖 long 档的 2.0",
          sil_exp < sil["long"], f"{sil_exp:.1f}s < {sil['long']:.1f}s")


# ============================================================ 6 story 组装
def test_story(work: str):
    print("\n[6] rdr_compose 三档预算不同(过场优先不变)")
    total = 1200.0
    tr = make_transcript(os.path.join(work, "rdr.json"), total,
                         ["任务让我们去抢劫火车", "他是谁为什么在这儿",
                          "开枪掩护我", "帅爆了这一枪"])
    make_metrics(os.path.join(work, "rdr.metrics.json"), total, tr, motion_base=40.0)
    # 3 段过场,各 60s
    cs = {"cuts": [{"start": 100.0, "end": 160.0, "dur": 60.0},
                   {"start": 500.0, "end": 560.0, "dur": 60.0},
                   {"start": 900.0, "end": 960.0, "dur": 60.0}]}
    json.dump(cs, open(os.path.join(work, "rdr.cutscenes.json"), "w",
                       encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"segments": []}, open(os.path.join(work, "rdr.voice.json"), "w",
                                     encoding="utf-8"))
    json.dump({"segments": []}, open(os.path.join(work, "rdr.game.json"), "w",
                                     encoding="utf-8"))

    tots = {}
    for tier in ("short", "medium", "long"):
        op = os.path.join(work, f"rdr_{tier}.cuts.json")
        run([os.path.join(HERE, "rdr_compose.py"), "--work", work,
             "--file", "rdr", "--tier", tier, "--out", op])
        tots[tier] = sum(c["dur"] for c in load(op)["cuts"])
    print(f"     成片 short {tots['short']:.0f}s / medium {tots['medium']:.0f}s / "
          f"long {tots['long']:.0f}s")
    check("story 三档总长递增",
          tots["short"] < tots["medium"] < tots["long"],
          f"{tots['short']:.0f} < {tots['medium']:.0f} < {tots['long']:.0f}")



# ============================================================ 7 文稿时间轴规整
def test_transcript_clamp(work: str):
    print("\n[7] 异常长句必须被规整(whisper 在稀疏对白上会把句子拉长)")
    tp = os.path.join(work, "bad.json")
    json.dump({"segments": [
        {"start": 0.0, "end": 402.6, "text": "合作吗"},        # 3 字跨 402 秒
        {"start": 500.0, "end": 501.8, "text": "正常的一句台词"},
        {"start": 600.0, "end": 931.9, "text": "你估计了的组织"},
    ]}, open(tp, "w", encoding="utf-8"), ensure_ascii=False)
    sys.path.insert(0, HERE)
    import transcript
    _, segs = transcript.load(tp, verbose=False)
    durs = [s["end"] - s["start"] for s in segs]
    print(f"     规整后句长: {[round(d, 1) for d in durs]}")
    check("402/931 秒的异常句被收到合理范围",
          all(d <= 12.0 for d in durs), f"max={max(durs):.1f}s")
    check("正常句(1.8s)不被误伤", abs(durs[1] - 1.8) < 0.05, f"{durs[1]:.2f}s")
    check("异常句保留了起始时间", segs[0]["start"] == 0.0, f"{segs[0]['start']}")


# ============================================================ 8 无对白战斗候选
def test_motion_candidates(work: str):
    print("\n[8] 动作类:无对白的战斗要能入选,安静的赶路不能")
    total = 600.0
    # 文稿只在 0~60s 有对白;中间 200~240s 是"响亮 + 高运动"(战斗),
    # 400~440s 是"安静 + 高运动"(赶路/运镜)
    tr = {"segments": [
        {"start": 5.0, "end": 8.0, "text": "格挡要抓时机"},
        {"start": 20.0, "end": 24.0, "text": "这一刀砍得漂亮"},
    ]}
    tp = os.path.join(work, "mc.json")
    json.dump(tr, open(tp, "w", encoding="utf-8"), ensure_ascii=False)
    wins = []
    for k in range(int(total)):
        loud, motion = -45.0, 10.0
        if 200 <= k < 240:
            loud, motion = -18.0, 95.0        # 战斗:响 + 动
        elif 400 <= k < 440:
            loud, motion = -50.0, 95.0        # 赶路:静 + 动
        wins.append({"motion": motion, "loud": loud,
                     "silent": loud < -45.0, "cut": 0, "dark": 0})
    mp = os.path.join(work, "mc.metrics.json")
    json.dump({"duration": total, "window": 1.0, "windows": wins},
              open(mp, "w", encoding="utf-8"), ensure_ascii=False)

    op = os.path.join(work, "mc.cuts.json")
    run([os.path.join(HERE, "pick.py"), "--transcript", tp, "--metrics", mp,
         "--genre", "action", "--tier", "medium", "--budget-sec", "120",
         "--top", "20", "--max-unit", "15", "--out", op])
    d = load(op)
    starts = [c["start"] for c in d["cuts"]]
    combat = [st for st in starts if 195 <= st <= 245]
    quiet = [st for st in starts if 395 <= st <= 445]
    print(f"     选段起点 {[round(x) for x in sorted(starts)]}")
    check("无对白战斗段(200~240s)被选中", bool(combat), f"{combat}")
    check("安静的赶路段(400~440s)被响度闸门排除", not quiet, f"{quiet}")
    check("战斗段带『画面动作』维度",
          any("画面动作" in c["dims"] for c in d["cuts"]), "ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    a = ap.parse_args()

    work = tempfile.mkdtemp(prefix="autocut_selftest_")
    print(f"video-autocut 自检   临时目录 {work}")
    try:
        outs = test_tiers(work)
        test_penalty(work)
        test_tighten(work, outs)
        test_story(work)
        test_transcript_clamp(work)
        test_motion_candidates(work)
    except Exception as e:            # noqa: BLE001  自检脚本要把原始错误完整露出
        print(f"\n💥 自检中断: {e}")
        if a.keep:
            print(f"临时目录保留:{work}")
        return 1
    finally:
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项,失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"  ❌ {f}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
