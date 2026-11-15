"""
verify_tracks_all.py —— 对全库每个文件逐轨转写验证,找出真正的解说轨

为什么不用能量:实测反例 —— 某文件轨1 能量 100% 但转写中文字 0(纯游戏音效),
真正的解说在能量仅 54% 的轨3。能量只能粗筛,ASR 才能定论。

输出:每个文件每轨的「中文字/分钟」,挑最高者为解说轨,并与批处理实际用的轨对比。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

import paths

FFMPEG = paths.ffmpeg()
FFPROBE = paths.ffprobe()
CJK = re.compile(r"[\u4e00-\u9fff]")


def audio_indices(p):
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "json", p],
                       capture_output=True, text=True, errors="replace")
    try:
        return [s["index"] for s in paths.parse_probe_json(r.stdout)["streams"]]
    except Exception:
        return []


def duration_of(p):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", p], capture_output=True, text=True, errors="replace")
    try:
        return float(paths.parse_probe_json(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def energy(p, ai, dur):
    act = []
    for f in [0.1, 0.3, 0.5, 0.7, 0.9]:
        r = subprocess.run([FFMPEG, "-hide_banner", "-nostats",
                            "-ss", f"{max(0.0, dur * f):.1f}", "-t", "15",
                            "-i", p, "-map", f"0:{ai}", "-vn", "-ac", "1", "-ar", "16000",
                            "-f", "f32le", "-"], capture_output=True)
        x = np.frombuffer(r.stdout, dtype=np.float32)
        if x.size < 4800:
            continue
        st = 2400
        n = x.size // st
        y = x[: n * st].reshape(n, st)
        ms = np.einsum("ij,ij->i", y, y, dtype=np.float64) / st
        db = 20 * np.log10(np.maximum(np.sqrt(ms), 1e-9))
        act.append(float((db > -35).mean()))
    return float(max(act)) if act else 0.0


def sample(p, ai, dur, seconds, dst):
    parts = []
    td = os.path.dirname(dst)
    for k, f in enumerate([0.2, 0.55, 0.85]):
        pp = os.path.join(td, f"x{ai}_{k}.wav")
        subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{max(0.0, dur * f):.1f}", "-t", f"{seconds:.0f}",
                        "-i", p, "-map", f"0:{ai}", "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", pp], capture_output=True)
        if os.path.exists(pp) and os.path.getsize(pp) > 8000:
            parts.append(pp)
    if not parts:
        return 0.0
    lst = os.path.join(td, f"l{ai}.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for pp in parts:
            fh.write(f"file '{pp.replace(chr(92), '/')}'\n")
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", dst],
                   capture_output=True)
    for pp in parts:
        try:
            os.remove(pp)
        except OSError:
            pass
    try:
        os.remove(lst)
    except OSError:
        pass
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", dst], capture_output=True, text=True, errors="replace")
    try:
        return float(paths.parse_probe_json(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--out", default="_track_verify.json")
    a = ap.parse_args()

    files = sorted(f for f in os.listdir(a.src) if f.lower().endswith((".mkv", ".mp4")))
    # 批处理实际用的轨
    used = {}
    for fn in files:
        b = os.path.splitext(fn)[0]
        side = os.path.join("_audio", b + ".track.json")
        if os.path.exists(side):
            used[fn] = json.load(open(side, encoding="utf-8")).get("track")

    from faster_whisper import WhisperModel
    import torch
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    ct = "float16" if dev == "cuda" else "int8"
    t0 = time.time()
    model = WhisperModel("small", device=dev, compute_type=ct)
    print(f"模型就绪 {time.time() - t0:.1f}s ({dev}/{ct})\n")

    tmp = tempfile.mkdtemp(prefix="trkverify_")
    results = []
    print(f"{'文件':<26} {'能量轨':>6} {'实际':>4}  各轨中文字/分钟                判定")
    print("-" * 100)
    for fn in files:
        p = os.path.join(a.src, fn)
        dur = duration_of(p)
        idxs = audio_indices(p)
        en = {}
        for ai in idxs:
            en[ai] = energy(p, ai, dur)
        en_max = max(en, key=lambda k: en[k]) if en else None

        cpm = {}
        for ai in idxs:
            wav = os.path.join(tmp, f"v{ai}.wav")
            sec = sample(p, ai, dur, a.seconds, wav)
            if sec < 3:
                cpm[ai] = 0.0
                continue
            segs, info = model.transcribe(wav, language=None, beam_size=5,
                                          vad_filter=True, condition_on_previous_text=False)
            txt = "".join(s.text for s in segs)
            cpm[ai] = round(len(CJK.findall(txt)) / (sec / 60.0), 1)

        best = max(cpm, key=lambda k: cpm[k]) if cpm else None
        u = used.get(fn)
        ok = (best == u)
        txt = " ".join(f"{k}:{v:5.1f}" for k, v in sorted(cpm.items()))
        print(f"{fn:<26} {str(en_max):>6} {str(u):>4}  {txt:<30} "
              f"{'✓ 一致' if ok else f'❌ 应改轨{best}'}")
        results.append({"file": fn, "used": u, "energy_max": en_max,
                        "energy": {str(k): round(v, 3) for k, v in en.items()},
                        "cpm": {str(k): v for k, v in cpm.items()},
                        "best": best, "ok": ok})
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)

    bad = [r for r in results if not r["ok"]]
    print("-" * 100)
    print(f"\n一致 {len(results) - len(bad)} 个 / 需修正 {len(bad)} 个")
    for r in bad:
        print(f"  {r['file']:<26} 实际轨{r['used']} -> 应改轨{r['best']}  "
              f"(实际 {r['cpm'].get(str(r['used']), 0):.1f} 字/分  "
              f"vs 最优 {r['cpm'].get(str(r['best']), 0):.1f} 字/分)")
    print(f"\n-> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
