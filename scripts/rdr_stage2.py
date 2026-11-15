r"""
rdr_stage2.py —— 大镖客第二阶段:转写解说轨 + 抽写游戏对白 + 画面分析

三个信号合起来定位"关键游玩段落":
  ① 解说轨文稿(你的声音)—— 稀疏,但是你的关注点
  ② 游戏对白轨文稿(英文)—— 剧情信息,NPC 对话/任务提示都在里面
  ③ 画面运动 —— 战斗/追逐等动作段落
过场黑边已经单独检测过了(第一阶段)。

用法:
  python rdr_stage2.py --src "G:\录像\dabiaoge" --work G:\autocut_work\dabiaoge
                       [--stage transcribe|analyze|all] [--fps 1]
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


def run(label, args, log=None, tail=8):
    print(f"\n{'=' * 72}\n[{time.strftime('%H:%M:%S')}] {label}\n{'=' * 72}", flush=True)
    t0 = time.time()
    r = subprocess.run([PY] + args, text=True, errors="replace",
                       encoding="utf-8", capture_output=True)
    for line in (r.stdout or "").strip().splitlines()[-tail:]:
        print("   ", line)
    if r.returncode != 0:
        print(f"   !! 退出码 {r.returncode}")
        for line in (r.stderr or "").strip().splitlines()[-6:]:
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
    ap.add_argument("--stage", default="all", choices=["all", "transcribe", "analyze"])
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--chunk-min", type=float, default=20.0)
    a = ap.parse_args()

    log = os.path.join(a.work, "stage2.log")
    files = sorted(f for f in os.listdir(a.src)
                   if f.lower().endswith((".mkv", ".mp4")))
    todo = []
    for fn in files:
        base = os.path.splitext(fn)[0]
        src = os.path.join(a.src, fn)
        # 跳过空文件
        if os.path.getsize(src) < 1e6:
            continue
        todo.append((fn, base, src))
    print(f"待处理 {len(todo)} 个文件")

    t_all = time.time()
    results = []
    for i, (fn, base, src) in enumerate(todo, 1):
        side = os.path.join(a.work, base + ".tracks.json")
        tracks = {}
        if os.path.exists(side):
            tracks = json.load(open(side, encoding="utf-8"))
        gav = os.path.join(a.work, base + ".game.wav")
        vv = os.path.join(a.work, base + ".voice.wav")
        tr_v = os.path.join(a.work, base + ".voice.json")
        tr_g = os.path.join(a.work, base + ".game.json")
        mt = os.path.join(a.work, base + ".metrics.json")

        print(f"\n\n{'#' * 72}\n### [{i}/{len(todo)}] {fn}\n{'#' * 72}")
        rec = {"file": fn, "game_track": tracks.get("game"),
               "voice_track": tracks.get("voice")}

        if a.stage in ("all", "transcribe"):
            # 解说轨(你的声音)
            if os.path.exists(vv):
                if not (os.path.exists(tr_v) and os.path.getsize(tr_v) > 3000):
                    run(f"转写解说 {fn}", [os.path.join(SKILL, "transcribe_long.py"),
                                          vv, "--out", tr_v,
                                          "--chunk-min", str(a.chunk_min)], log)
                if os.path.exists(tr_v):
                    try:
                        j = json.load(open(tr_v, encoding="utf-8"))
                        rec["voice_sentences"] = len(j.get("segments") or [])
                        rec["voice_lang"] = j.get("meta", {}).get("language")
                    except Exception:
                        pass
            # 游戏对白轨(英文,剧情信息)
            if os.path.exists(gav):
                if not (os.path.exists(tr_g) and os.path.getsize(tr_g) > 3000):
                    run(f"转写游戏对白 {fn}", [os.path.join(SKILL, "transcribe_long.py"),
                                             gav, "--out", tr_g, "--language", "en",
                                             "--chunk-min", str(a.chunk_min)], log)
                if os.path.exists(tr_g):
                    try:
                        j = json.load(open(tr_g, encoding="utf-8"))
                        rec["game_sentences"] = len(j.get("segments") or [])
                    except Exception:
                        pass

        if a.stage in ("all", "analyze"):
            if not (os.path.exists(mt) and os.path.getsize(mt) > 1000):
                run(f"画面分析 {fn}", [os.path.join(SKILL, "analyze.py"), src,
                                      "--audio", gav if os.path.exists(gav) else "",
                                      "--fps", str(a.fps), "--hw", "--out", mt], log)
            if os.path.exists(mt):
                try:
                    d = json.load(open(mt, encoding="utf-8"))
                    rec["duration"] = d.get("duration")
                    rec["cut_count"] = d.get("cut_count")
                except Exception:
                    pass

        results.append(rec)
        with open(os.path.join(a.work, "_stage2.json"), "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)

    print(f"\n\n{'=' * 72}\n完成,耗时 {(time.time() - t_all) / 60:.0f} 分钟\n{'=' * 72}")
    for r in results:
        print(f"  {r['file']:<28} 解说句{r.get('voice_sentences', '-'):>5}  "
              f"对白句{r.get('game_sentences', '-'):>6}  "
              f"时长{(r.get('duration') or 0) / 60:6.1f}分  切换{r.get('cut_count', '-')}")


if __name__ == "__main__":
    raise SystemExit(main())
