r"""
batch_all.py —— 全库批处理:转写 → 画面分析 → 选段 → 压紧 → 导出 → 生成草稿

设计要点:
  - 分阶段串行,每阶段的产物落盘,中断后重跑会跳过已完成部分
  - 画面分析用 --hw 硬解;跳过无解说的文件(靠 --skip 指定)
  - 每个文件生成一个独立草稿,名字 = MC_<日期时间>
  - 转写结果自检:句数过少或语言异常会打印警告(说明解说轨可能挑错)

用法:
  python batch_all.py --src "G:\录像\MC" --audio _audio --work _batch
                      [--skip 2026-07-03_22-50-34] [--genre survival]
                      [--stage transcribe|analyze|pick|cut|draft|all]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SKILL = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(label: str, args: list[str], log_path: str | None = None) -> int:
    print(f"\n{'=' * 78}\n[{time.strftime('%H:%M:%S')}] {label}\n{'=' * 78}", flush=True)
    t0 = time.time()
    r = subprocess.run([PY] + args, text=True, errors="replace",
                       encoding="utf-8", capture_output=True)
    el = time.time() - t0
    tail = (r.stdout or "").strip().splitlines()
    for line in tail[-14:]:
        print("   ", line)
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        print(f"   !! 退出码 {r.returncode}")
        for line in err[-10:]:
            print("   E", line)
    print(f"   ({el:.0f}s)")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {label} =====\n")
            f.write(r.stdout or "")
            f.write("\n--- stderr ---\n")
            f.write(r.stderr or "")
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--genre", default="survival")
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--stage", default="all",
                    choices=["all", "transcribe", "analyze", "pick", "cut", "draft"])
    ap.add_argument("--max-gap", type=float, default=1.2)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--budget-ratio", type=float, default=0.15,
                    help="每场成片总长占素材比例(默认 0.15 = 15%%)。"
                         "按各文件实际时长自动折算成秒数,作为硬上限传给 pick.py")
    ap.add_argument("--max-unit", type=float, default=150.0,
                    help="单个片段时长上限(秒,默认 150)。超了就按呼吸口/强制切分,"
                         "保证各场次节奏均匀")
    a = ap.parse_args()

    os.makedirs(a.work, exist_ok=True)
    log = os.path.join(a.work, "batch.log")
    files = sorted(f for f in os.listdir(a.src) if f.lower().endswith((".mkv", ".mp4")))
    print(f"全库 {len(files)} 个文件,跳过 {len(a.skip)} 个,品类 {a.genre}")
    print(f"工作目录 {a.work}")
    print(f"日志 {log}")

    t_start = time.time()
    summary = []

    for i, fn in enumerate(files, 1):
        base = os.path.splitext(fn)[0]
        src = os.path.join(a.src, fn)
        wav = os.path.join(a.audio, base + ".wav")
        side = os.path.join(a.audio, base + ".track.json")
        tr = os.path.join(a.work, base + ".json")
        mt = os.path.join(a.work, base + ".metrics.json")
        cuts = os.path.join(a.work, base + f".{a.genre}.cuts.json")
        tight = os.path.join(a.work, base + ".tight.json")
        clips = os.path.join(a.work, "_clips", base)

        if any(s in base for s in a.skip):
            print(f"\n### [{i}/{len(files)}] {fn}  -> 跳过(在 --skip 列表里)")
            summary.append({"file": fn, "status": "skipped"})
            continue
        if not os.path.exists(wav):
            print(f"\n### [{i}/{len(files)}] {fn}  -> 缺 wav,跳过")
            summary.append({"file": fn, "status": "no-audio"})
            continue

        track = None
        if os.path.exists(side):
            track = json.load(open(side, encoding="utf-8")).get("track")

        # 该文件的成片预算(秒)= 素材时长 × 比例
        budget_sec = 0.0
        if os.path.exists(mt):
            try:
                budget_sec = json.load(open(mt, encoding="utf-8"))["duration"] * a.budget_ratio
            except Exception:
                budget_sec = 0.0

        print(f"\n\n{'#' * 78}")
        print(f"### [{i}/{len(files)}] {fn}   解说轨 {track}   开始 {time.strftime('%H:%M:%S')}")
        print(f"{'#' * 78}")

        rec = {"file": fn, "track": track}
        stages = ("transcribe", "analyze", "pick", "cut", "draft") \
            if a.stage == "all" else (a.stage,)

        # 断点续跑:转写 / 画面 / 切点 / 压紧 / 片段 全部算完且已生成过草稿,
        # 就整个文件跳过。没有这道判断会重跑选段(切点是随机 id 的草稿,
        # 重算等于白烧几小时)。
        clips_done = os.path.isdir(clips) and any(
            f.endswith(".mp4") for f in os.listdir(clips)) if os.path.isdir(clips) else False
        if (os.path.exists(tr) and os.path.exists(mt)
                and os.path.exists(tight) and clips_done):
            print(f"\n### [{i}/{len(files)}] {fn}  -> 产物齐全,整文件跳过")
            try:
                j = json.load(open(tr, encoding="utf-8"))
                rec["sentences"] = len(j.get("segments") or [])
                rec["lang"] = j.get("meta", {}).get("language")
            except Exception:
                pass
            try:
                c = json.load(open(tight, encoding="utf-8"))["cuts"]
                rec["cuts"] = len(c)
                rec["cut_sec"] = round(sum(x["dur"] for x in c), 1)
                rec["final_segments"] = len(c)
            except Exception:
                pass
            rec["status"] = "cached"
            summary.append(rec)
            with open(os.path.join(a.work, "_summary.json"), "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=1)
            continue

        for st in stages:
            if st == "transcribe":
                if os.path.exists(tr) and os.path.getsize(tr) > 5000:
                    try:
                        j = json.load(open(tr, encoding="utf-8"))
                        n = len(j.get("segments") or [])
                        print(f"\n[转写] 已有产物,{n} 句,跳过")
                        rec["sentences"] = n
                        rec["lang"] = j.get("meta", {}).get("language")
                        continue
                    except Exception:
                        pass
                rc = run(f"转写 {fn}", [os.path.join(SKILL, "transcribe_long.py"), wav,
                                      "--out", tr, "--chunk-min", "20"], log)
                rec["transcribe_rc"] = rc
                if os.path.exists(tr):
                    try:
                        j = json.load(open(tr, encoding="utf-8"))
                        n = len(j.get("segments") or [])
                        lang = j.get("meta", {}).get("language")
                        rec["sentences"], rec["lang"] = n, lang
                        # 自检:句数过少 = 解说轨可能挑错
                        dur_h = (j.get("meta", {}).get("duration_sec") or 1) / 3600
                        if n < dur_h * 20:
                            print(f"   ⚠️ 自检:{dur_h:.1f} 小时只有 {n} 句,"
                                  f"解说轨 {track} 可能挑错!")
                            rec["suspect"] = True
                    except Exception as e:
                        print(f"   产物解析失败: {e}")

            elif st == "analyze":
                if os.path.exists(mt) and os.path.getsize(mt) > 1000:
                    print(f"\n[画面] 已有产物,跳过")
                    continue
                rc = run(f"画面分析 {fn}", [os.path.join(SKILL, "analyze.py"), src,
                                          "--audio", wav, "--fps", "1", "--hw",
                                          "--out", mt], log)
                rec["analyze_rc"] = rc

            elif st == "pick":
                if not (os.path.exists(tr) and os.path.exists(mt)):
                    print(f"\n[选段] 缺转写或画面产物,跳过")
                    continue
                if os.path.exists(cuts) and os.path.getsize(cuts) > 500:
                    print(f"\n[选段] 已有切点产物,跳过")
                    try:
                        c = json.load(open(cuts, encoding="utf-8"))["cuts"]
                        rec["cuts"] = len(c)
                        rec["cut_sec"] = round(sum(x["dur"] for x in c), 1)
                    except Exception:
                        pass
                    continue
                rc = run(f"选段 {fn}", [os.path.join(SKILL, "pick.py"),
                                       "--transcript", tr, "--metrics", mt,
                                       "--genre", a.genre, "--top", str(a.top),
                                       "--budget-sec", str(round(budget_sec, 1)),
                                       "--max-unit", str(round(a.max_unit, 1)),
                                       "--out", cuts], log)
                rec["pick_rc"] = rc
                if os.path.exists(cuts):
                    try:
                        c = json.load(open(cuts, encoding="utf-8"))["cuts"]
                        rec["cuts"] = len(c)
                        rec["cut_sec"] = round(sum(x["dur"] for x in c), 1)
                    except Exception:
                        pass

            elif st == "tight":
                pass

            elif st == "cut":
                if not os.path.exists(cuts):
                    print(f"\n[导出] 缺切点,跳过")
                    continue
                rc = run(f"压紧节奏 {fn}", [os.path.join(SKILL, "tighten.py"),
                                           "--cuts", cuts, "--transcript", tr,
                                           "--metrics", mt, "--mode", "gap",
                                           "--max-gap", str(a.max_gap),
                                           "--min-seg", "4", "--out", tight], log)
                rec["tighten_rc"] = rc
                use = tight if os.path.exists(tight) else cuts
                rec["final_segments"] = len(json.load(open(use, encoding="utf-8"))["cuts"])
                rc = run(f"导出片段 {fn}", [os.path.join(SKILL, "cut_clips.py"),
                                           "--cuts", use, "--src", src,
                                           "--track", str(track), "--dst", clips], log)
                rec["cut_rc"] = rc

            elif st == "draft":
                if not os.path.isdir(clips):
                    print(f"\n[草稿] 缺片段,跳过")
                    continue
                mp4s = sorted(f for f in os.listdir(clips) if f.endswith(".mp4"))
                if not mp4s:
                    print(f"\n[草稿] 片段目录为空,跳过")
                    continue
                name = "MC_" + base.replace("2026-", "").replace("_", "-")
                stage_dir = os.path.join(a.work, "_stage", name)
                root = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                    r"JianyingPro\User Data\Projects\com.lveditor.draft")
                os.environ["AUTOCUT_ALLOW_REPLACE"] = "1"
                args = [os.path.join(SKILL, "make_draft.py"), "--name", name,
                        "--out-root", os.path.join(a.work, "_stage"),
                        "--final-root", root]
                for m in mp4s:
                    args += ["--clip", os.path.join(clips, m)]
                rc = run(f"生成草稿 {name}", args, log)
                rec["draft_rc"] = rc
                if rc == 0:
                    vrc = run(f"校验 {name}", [os.path.join(SKILL, "validate_draft.py"),
                                              stage_dir], log)
                    rec["validate_rc"] = vrc
                    if vrc == 0:
                        irc = run(f"装入 {name}", [os.path.join(SKILL, "install_testdraft.py"),
                                                  stage_dir], log)
                        rec["install_rc"] = irc
                        rec["draft_name"] = name

        summary.append(rec)
        with open(os.path.join(a.work, "_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)

    el = (time.time() - t_start) / 60
    print(f"\n\n{'=' * 78}")
    print(f"全库批处理完成,耗时 {el:.0f} 分钟")
    print(f"{'=' * 78}")
    ok = [r for r in summary if r.get("draft_name")]
    sus = [r for r in summary if r.get("suspect")]
    print(f"成功生成草稿 {len(ok)} 个 / 共 {len(summary)} 个文件")
    for r in summary:
        tag = "OK " if r.get("draft_name") else "   "
        w = " ⚠️解说轨存疑" if r.get("suspect") else ""
        print(f"  [{tag}] {r['file']:<26} 轨{r.get('track')} "
              f"{r.get('sentences', '-')}句 {r.get('cuts', '-')}段 "
              f"{r.get('cut_sec', '-')}s{w}")
    if sus:
        print(f"\n⚠️ {len(sus)} 个文件的转写句数偏少,解说轨可能挑错 —— 需复查")
    print(f"\n汇总 -> {os.path.join(a.work, '_summary.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
