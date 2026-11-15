"""
per_track.py —— 逐条音轨分析(关键修正)

OBS 分轨录制会在一个 MKV 里放 4 条独立音轨(simple_aac_recording0..3):
分别是游戏声、麦克风、系统声、浏览器等。之前只取默认轨(stream a:0),
所以把"解说在别的轨上"误判成了"几乎静音"。

本脚本对每条音轨分别测响度/活跃度,找出哪条是人声。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

FFMPEG = paths.ffmpeg()
FFPROBE = paths.ffprobe()
SRC = r"G:\录像\MC"


def audio_stream_indices(path: str) -> list[int]:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "json", path],
                       capture_output=True, text=True, errors="replace")
    try:
        return [s["index"] for s in paths.parse_probe_json(r.stdout)["streams"]]
    except Exception:
        return []


def track_stats(path: str, aidx: int, start: float, dur: float = 180.0) -> dict:
    """取一段时间,把该音轨混成单声道测统计。"""
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-ss", str(start), "-t", str(dur),
           "-i", path, "-map", f"0:{aidx}", "-vn",
           "-ac", "1", "-ar", "8000", "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    x = np.frombuffer(r.stdout, dtype=np.float32)
    if x.size < 8000:
        return {}
    step = 2000
    n = x.size // step
    y = x[: n * step].reshape(n, step)
    ms = np.einsum("ij,ij->i", y, y, dtype=np.float64) / step
    db = 20 * np.log10(np.maximum(np.sqrt(ms), 1e-9))
    return {
        "rms": round(float(20 * np.log10(max(float(np.sqrt(ms.mean())), 1e-9))), 1),
        "peak": round(float(20 * np.log10(max(float(np.abs(x).max()), 1e-9))), 1),
        "active35": round(float((db > -35).mean()), 3),
        "active45": round(float((db > -45).mean()), 3),
        "p90": round(float(np.percentile(db, 90)), 1),
    }


files = sorted(f for f in os.listdir(SRC) if f.endswith(".mkv"))
print(f"逐轨分析 {len(files)} 个文件(每个取 2 段各 3 分钟)\n")

summary = []
for fn in files:
    p = os.path.join(SRC, fn)
    idxs = audio_stream_indices(p)
    print(f"=== {fn}   共 {len(idxs)} 条音轨")
    best = None
    for ai in idxs:
        segs = []
        for st in (300.0, 1800.0):
            s = track_stats(p, ai, st)
            if s:
                segs.append(s)
        if not segs:
            print(f"   轨道 {ai}: 无数据")
            continue
        rms = sum(s["rms"] for s in segs) / len(segs)
        act = max(s["active35"] for s in segs)
        pk = max(s["peak"] for s in segs)
        print(f"   轨道 {ai}: RMS {rms:>7.1f} dB  峰值 {pk:>6.1f} dB  "
              f"活跃(>-35dB) {act * 100:>5.1f}%")
        if best is None or act > best[1]:
            best = (ai, act, rms)
    if best:
        print(f"   -> 最可能是人声: 轨道 {best[0]}  (活跃度 {best[1] * 100:.1f}%)")
        summary.append({"file": fn, "track": best[0], "active": best[1], "rms": best[2]})
    print()

with open("_per_track.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=1)

print("=" * 74)
voiced = [s for s in summary if s["active"] > 0.3]
print(f"有人声轨道的素材: {len(voiced)}/{len(summary)}")
for s in voiced:
    print(f"   {s['file']:<30} 轨道{s['track']}  活跃度 {s['active'] * 100:.1f}%")
