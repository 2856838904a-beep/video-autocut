"""
batch_rdr_export.py —— 大镖客全库导出 + 生成草稿(含解说参考轨)

前提:rdr_compose.py 已为每个文件产出 <base>.rdr.cuts.json

每个文件产出一个草稿:
  视频轨  = 过场 + 关键游玩片段(游戏原声)
  音频轨  = 「解说参考」,把该场的解说轨整条铺上去作参考(不混入主轨)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SKILL = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
ROOT = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                    r"JianyingPro\User Data\Projects\com.lveditor.draft")


def run(label, args, log=None, tail=6):
    print(f"\n{'=' * 72}\n[{time.strftime('%H:%M:%S')}] {label}\n{'=' * 72}", flush=True)
    t0 = time.time()
    r = subprocess.run([PY] + args, text=True, errors="replace",
                       encoding="utf-8", capture_output=True)
    for line in (r.stdout or "").strip().splitlines()[-tail:]:
        print("   ", line)
    if r.returncode != 0:
        print(f"   !! 退出码 {r.returncode}")
        for line in (r.stderr or "").strip().splitlines()[-5:]:
            print("   E", line)
    print(f"   ({time.time() - t0:.0f}s)")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n===== {label} =====\n{r.stdout or ''}\n--- stderr ---\n{r.stderr or ''}\n")
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--stage-root", default=None,
                    help="草稿生成到哪里(默认 <work>/_stage)")
    ap.add_argument("--final-root", default=ROOT)
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--no-voice-track", action="store_true",
                    help="不生成解说参考轨")
    a = ap.parse_args()

    log = os.path.join(a.work, "export.log")
    stage_root = a.stage_root or os.path.join(a.work, "_stage")
    os.makedirs(stage_root, exist_ok=True)
    os.environ["AUTOCUT_ALLOW_REPLACE"] = "1"

    files = sorted(f for f in os.listdir(a.src) if f.lower().endswith((".mkv", ".mp4")))
    results = []
    for i, fn in enumerate(files, 1):
        base = os.path.splitext(fn)[0]
        if any(s in base for s in a.skip):
            print(f"\n### [{i}/{len(files)}] {fn} 跳过")
            continue
        src = os.path.join(a.src, fn)
        cuts = os.path.join(a.work, base + ".rdr.cuts.json")
        if not os.path.exists(cuts):
            print(f"\n### [{i}/{len(files)}] {fn} 无切点,跳过")
            results.append({"file": fn, "ok": False, "why": "no-cuts"})
            continue
        n_cuts = len(json.load(open(cuts, encoding="utf-8"))["cuts"])
        if n_cuts == 0:
            print(f"\n### [{i}/{len(files)}] {fn} 切点为 0,跳过")
            results.append({"file": fn, "ok": False, "why": "empty"})
            continue

        # 游戏原声轨
        side = os.path.join(a.work, base + ".tracks.json")
        gtrack = 1
        if os.path.exists(side):
            gtrack = json.load(open(side, encoding="utf-8")).get("game") or 1

        clips = os.path.join(a.work, "_clips", base)
        print(f"\n\n{'#' * 72}\n### [{i}/{len(files)}] {fn}   {n_cuts} 段  游戏轨 {gtrack}\n{'#' * 72}")
        run(f"导出片段 {fn}", [os.path.join(SKILL, "cut_clips.py"), "--cuts", cuts,
                              "--src", src, "--track", str(gtrack),
                              "--dst", clips], log)

        mp4s = sorted(glob.glob(os.path.join(clips, "*.mp4")))
        if not mp4s:
            results.append({"file": fn, "ok": False, "why": "no-clips"})
            continue

        name = "RDR_" + base.replace("2026-", "").replace("_", "-")
        stage = os.path.join(stage_root, name)
        args = [os.path.join(SKILL, "make_draft.py"), "--name", name,
                "--out-root", stage_root, "--final-root", a.final_root]
        for m in mp4s:
            args += ["--clip", m]
        # 解说参考轨
        wav = os.path.join(a.work, base + ".voice.wav")
        if not a.no_voice_track and os.path.exists(wav):
            args += ["--extra-audio", f"0.0|{wav}|解说参考"]
        rc = run(f"生成草稿 {name}", args, log)
        rec = {"file": fn, "ok": rc == 0, "cuts": n_cuts, "clips": len(mp4s),
               "draft_name": name, "game_track": gtrack}
        if rc == 0:
            v = run(f"校验 {name}", [os.path.join(SKILL, "validate_draft.py"), stage],
                    log, tail=3)
            rec["validate"] = v
        results.append(rec)
        with open(os.path.join(a.work, "_export.json"), "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)

    print(f"\n\n{'=' * 72}\n导出完成\n{'=' * 72}")
    for r in results:
        flag = "OK " if r.get("ok") else "!! "
        print(f"  [{flag}] {r['file']:<28} {r.get('cuts', '-'):>4} 段  "
              f"{r.get('clips', '-'):>4} 片段  {r.get('draft_name') or r.get('why', '')}")


if __name__ == "__main__":
    raise SystemExit(main())
