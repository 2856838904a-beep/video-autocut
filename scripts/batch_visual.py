r"""
batch_visual.py —— 对无语音素材跑「纯画面选段 → 导出 → 草稿」

适用:四轨 ASR 全部 0 字的素材(真的没人说话)。文稿选段完全失效,
只能靠画面运动。流程与 batch_all 一致,只是选段换成 pick_visual.py。

用法:
  python batch_visual.py --src "G:\录像\MC" --audio _audio_final --work _batch_v2
                         --files a.mkv b.mkv [--budget-sec 180]
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


def run(label, args, log=None):
    print(f"\n{'=' * 74}\n[{time.strftime('%H:%M:%S')}] {label}\n{'=' * 74}", flush=True)
    t0 = time.time()
    r = subprocess.run([PY] + args, text=True, errors="replace",
                       encoding="utf-8", capture_output=True)
    for line in (r.stdout or "").strip().splitlines()[-12:]:
        print("   ", line)
    if r.returncode != 0:
        print(f"   !! 退出码 {r.returncode}")
        for line in (r.stderr or "").strip().splitlines()[-8:]:
            print("   E", line)
    print(f"   ({time.time() - t0:.0f}s)")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n===== {label} =====\n{r.stdout or ''}\n--- stderr ---\n{r.stderr or ''}\n")
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--budget-sec", type=float, default=240.0)
    ap.add_argument("--max-unit", type=float, default=100.0)
    ap.add_argument("--top", type=int, default=14)
    a = ap.parse_args()

    log = os.path.join(a.work, "visual.log")
    root = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        r"JianyingPro\User Data\Projects\com.lveditor.draft")
    os.environ["AUTOCUT_ALLOW_REPLACE"] = "1"
    results = []

    for i, fn in enumerate(a.files, 1):
        base = os.path.splitext(fn)[0]
        src = os.path.join(a.src, fn)
        side = os.path.join(a.audio, base + ".track.json")
        mt = os.path.join(a.work, base + ".metrics.json")
        cuts = os.path.join(a.work, base + ".visual.cuts.json")
        tight = os.path.join(a.work, base + ".visual.tight.json")
        clips = os.path.join(a.work, "_clips", base)
        print(f"\n\n{'#' * 74}\n### [{i}/{len(a.files)}] {fn}  (纯画面)\n{'#' * 74}")

        if not os.path.exists(mt):
            run(f"画面分析 {fn}", [os.path.join(SKILL, "analyze.py"), src,
                                  "--audio", os.path.join(a.audio, base + ".wav"),
                                  "--fps", "1", "--hw", "--out", mt], log)

        run(f"画面选段 {fn}", [os.path.join(SKILL, "pick_visual.py"),
                              "--metrics", mt, "--top", str(a.top),
                              "--budget-sec", str(a.budget_sec),
                              "--max-unit", str(a.max_unit), "--out", cuts], log)

        if not os.path.exists(cuts):
            print("   选段失败,跳过")
            results.append({"file": fn, "ok": False})
            continue
        nc = len(json.load(open(cuts, encoding="utf-8"))["cuts"])

        # 压紧(画面选段里也去空隙)
        tr = os.path.join(a.work, base + ".json")
        args = [os.path.join(SKILL, "tighten.py"), "--cuts", cuts,
                "--metrics", mt, "--mode", "gap", "--max-gap", "1.2",
                "--min-seg", "3", "--out", tight]
        if os.path.exists(tr):
            args[3:3] = ["--transcript", tr]
        run(f"压紧 {fn}", args, log)
        use = tight if os.path.exists(tight) else cuts

        track = 1
        if os.path.exists(side):
            try:
                track = json.load(open(side, encoding="utf-8")).get("track") or 1
            except Exception:
                pass

        run(f"导出片段 {fn}", [os.path.join(SKILL, "cut_clips.py"),
                               "--cuts", use, "--src", src,
                               "--track", str(track), "--dst", clips], log)

        mp4s = sorted(f for f in os.listdir(clips)
                      if f.endswith(".mp4")) if os.path.isdir(clips) else []
        if not mp4s:
            results.append({"file": fn, "ok": False, "cuts": nc})
            continue

        name = "MC_" + base.replace("2026-", "").replace("_", "-")
        stage = os.path.join(a.work, "_stage", name)
        args = [os.path.join(SKILL, "make_draft.py"), "--name", name,
                "--out-root", os.path.join(a.work, "_stage"), "--final-root", root]
        for mp4 in mp4s:
            args += ["--clip", os.path.join(clips, mp4)]
        rc = run(f"生成草稿 {name}", args, log)
        if rc == 0:
            run(f"装入 {name}", [os.path.join(SKILL, "install_testdraft.py"), stage], log)
        results.append({"file": fn, "ok": rc == 0, "cuts": nc,
                        "clips": len(mp4s), "draft_name": name})

    print(f"\n\n{'=' * 74}\n画面选段批处理完成\n{'=' * 74}")
    for r in results:
        print(f"  [{'OK ' if r.get('ok') else '   '}] {r['file']:<28} "
              f"{r.get('cuts', '-')} 段  {r.get('clips', '-')} 片段  {r.get('draft_name') or ''}")


if __name__ == "__main__":
    raise SystemExit(main())
