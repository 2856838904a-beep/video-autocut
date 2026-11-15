"""
batch_rdr.py —— 荒野大镖客(单机 3A 故事向)全流程

与 MC 那批的区别:
  1. 选段信号是「过场动画黑边」(+ 你的解说密度),不是玩法的物资/计划维度
  2. 你的解说轨极轻(-70~-85 dB),但要**单独放一条音频轨道作参考**,不混入主轨
  3. 每场素材短(45 分钟~3 小时),总规模 17.4 小时

阶段:
  ① 逐轨 ASR 挑轨(游戏原声轨 + 解说轨)
  ② 抽两条音轨(game / voice)
  ③ 过场动画检测(黑边)
  ④ 抽写文稿(用于找"关键游玩段落")
  ⑤ 画面运动分析
  ⑥ 合成选段:过场全收 + 关键游玩段落
  ⑦ 导出片段(画面 + 游戏原声)
  ⑧ 生成草稿
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
import paths

FFMPEG = paths.ffmpeg()
FFPROBE = paths.ffprobe()
def run(label, args, log=None, tail=10):
    print(f"\n{'=' * 74}\n[{time.strftime('%H:%M:%S')}] {label}\n{'=' * 74}", flush=True)
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


def probe(p):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_format", "-show_streams",
                        "-of", "json", p], capture_output=True, text=True, errors="replace")
    try:
        j = paths.parse_probe_json(r.stdout)
    except Exception:
        return None
    d = float(j.get("format", {}).get("duration") or 0)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), {})
    auds = [s["index"] for s in j["streams"] if s["codec_type"] == "audio"]
    return {"dur": d, "codec": v.get("codec_name"), "audio": auds}


def pick_tracks(src, dur):
    """返回 (游戏原声轨, 解说轨)。判据:ASR 语言 —— en 的是游戏原声,zh 的是解说。"""
    sys.path.insert(0, SKILL)
    import extract_audio as ea
    import re
    idxs = ea.audio_indices(src)
    scores = ea.asr_scores(src, idxs, dur, seconds=40.0)
    cjk = re.compile(r"[\u4e00-\u9fff]")
    game, voice = None, None
    best_game_energy = -1
    rows = []
    for ai in idxs:
        st = scores.get(ai) or {}
        txt = st.get("text") or ""
        n_cjk = len(cjk.findall(txt))
        e = ea.track_activity(src, ai, dur)
        rows.append((ai, e, st.get("cpm", 0.0), n_cjk, txt[:40]))
        # 游戏原声:有能量、有语音段但不是中文
        if e > 0.15 and n_cjk == 0 and e > best_game_energy:
            best_game_energy, game = e, ai
        # 解说:有中文
        if n_cjk > 0 and (voice is None or st.get("cpm", 0) >
                          next((r[2] for r in rows if r[0] == voice), 0)):
            voice = ai
    if game is None and idxs:
        game = max(idxs, key=lambda k: ea.track_activity(src, k, dur))
    return game, voice, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--budget-ratio", type=float, default=0.20)
    ap.add_argument("--top", type=int, default=24)
    ap.add_argument("--max-unit", type=float, default=180.0)
    ap.add_argument("--stage", default="all",
                    choices=["all", "tracks", "cutscene", "transcribe", "analyze", "plan"])
    a = ap.parse_args()

    os.makedirs(a.work, exist_ok=True)
    os.makedirs(os.path.join(a.work, "_audio"), exist_ok=True)
    log = os.path.join(a.work, "batch.log")

    files = sorted(f for f in os.listdir(a.src) if f.lower().endswith((".mkv", ".mp4")))
    print(f"全库 {len(files)} 个文件")
    summary = []

    for i, fn in enumerate(files, 1):
        base = os.path.splitext(fn)[0]
        src = os.path.join(a.src, fn)
        info = probe(src)
        if not info:
            print(f"\n### [{i}/{len(files)}] {fn} 探测失败,跳过")
            summary.append({"file": fn, "status": "probe-failed"})
            continue
        if info["dur"] < 60:
            print(f"\n### [{i}/{len(files)}] {fn} 空文件({info['dur']:.0f}s),跳过")
            summary.append({"file": fn, "status": "empty"})
            continue

        gav = os.path.join(a.work, base + ".game.wav")
        vv = os.path.join(a.work, base + ".voice.wav")
        side = os.path.join(a.work, base + ".tracks.json")
        cs = os.path.join(a.work, base + ".cutscenes.json")
        tr = os.path.join(a.work, base + ".json")
        mt = os.path.join(a.work, base + ".metrics.json")

        print(f"\n\n{'#' * 74}")
        print(f"### [{i}/{len(files)}] {fn}   {info['dur'] / 3600:.2f}h   {info['codec']}")
        print(f"{'#' * 74}")
        rec = {"file": fn, "dur": info["dur"], "codec": info["codec"]}

        # ① 挑轨
        if os.path.exists(side):
            t = json.load(open(side, encoding="utf-8"))
            game, voice = t.get("game"), t.get("voice")
            print(f"[挑轨] 已有结果: 游戏轨 {game}  解说轨 {voice}")
        else:
            t0 = time.time()
            game, voice, rows = pick_tracks(src, info["dur"])
            print(f"[挑轨] 游戏原声轨 {game}   解说轨 {voice}   ({time.time() - t0:.0f}s)")
            for ai, e, cpm, n, txt in rows:
                tag = []
                if ai == game:
                    tag.append("游戏")
                if ai == voice:
                    tag.append("解说")
                print(f"    轨{ai}: 能量{e * 100:5.1f}%  字/分{cpm:6.1f}  中文字{n:>4}  "
                      f"{'/'.join(tag):<6} {txt}")
            with open(side, "w", encoding="utf-8") as f:
                json.dump({"game": game, "voice": voice,
                           "rows": [{"track": r[0], "energy": r[1], "cpm": r[2],
                                     "cjk": r[3], "text": r[4]} for r in rows]},
                          f, ensure_ascii=False, indent=1)
        rec["game_track"], rec["voice_track"] = game, voice

        # ② 抽两条音轨
        for aidx, dst, name in ((game, gav, "游戏原声"), (voice, vv, "解说")):
            if aidx is None or (os.path.exists(dst) and os.path.getsize(dst) > 10000):
                continue
            subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                            "-i", src, "-map", f"0:{aidx}", "-vn", "-ac", "1", "-ar", "16000",
                            "-c:a", "pcm_s16le", dst], capture_output=True)
            if os.path.exists(dst):
                print(f"[抽轨] {name}(轨{aidx}) -> {os.path.getsize(dst) / 1e6:.1f} MB")

        # ③ 过场检测
        if not os.path.exists(cs):
            run(f"过场检测 {fn}", [os.path.join(SKILL, "detect_cutscenes.py"),
                                  "--src", src, "--min", "6", "--out", cs], log, tail=12)
        if os.path.exists(cs):
            d = json.load(open(cs, encoding="utf-8"))
            rec["cutscenes"] = len(d["cuts"])
            rec["cutscene_sec"] = round(sum(c["dur"] for c in d["cuts"]), 1)
            print(f"[过场] {rec['cutscenes']} 段 / {rec['cutscene_sec'] / 60:.1f} 分钟")

        summary.append(rec)
        with open(os.path.join(a.work, "_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)

    print(f"\n\n{'=' * 74}\n完成\n{'=' * 74}")
    for r in summary:
        print(f"  {r['file']:<28} {r.get('dur', 0) / 3600:5.2f}h  "
              f"游戏轨{r.get('game_track')} 解说轨{r.get('voice_track')}  "
              f"过场 {r.get('cutscenes', '-')} 段 / "
              f"{(r.get('cutscene_sec') or 0) / 60:.1f} 分")


if __name__ == "__main__":
    raise SystemExit(main())
