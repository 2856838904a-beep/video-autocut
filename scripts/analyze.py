"""
analyze.py —— 长素材技术质量分析(无需 GPU、无需转写)

一条 ffmpeg 解码通道 + numpy 向量化计算,产出每个 1 秒窗口的客观指标:
  loud      : 该秒瞬时响度(dB,EBU R128 口径附近),找爆点/冷场
  silent    : 静音(口播卡壳、长时间空白)
  motion    : 画面运动强度(逐帧差分,0~100),高 = 动作激烈/镜头切换
  cut       : 是否镜头切换
  dark      : 是否接近黑场

设计要点:
  - 逐帧差分在 160x90 灰度上做,26 分钟素材约 10 秒跑完
  - 运动值做了「跨镜头归一化」:同一素材内可比,不同素材不可直接比
  - 所有时间轴都是秒,与原视频严格对齐

用法:
  python analyze.py <video> [--out metrics.json] [--fps 4]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time

import numpy as np

import paths

FFMPEG = paths.ffmpeg()
FFPROBE = paths.ffprobe()
W, H = 160, 90


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stderr or "") + (r.stdout or "")


def probe_duration(video: str) -> tuple[float, bool]:
    """返回 (时长秒, 是否有音轨)。"""
    log = _run([FFPROBE, "-v", "error", "-show_entries",
                "format=duration:stream=codec_type", "-of", "json", video])
    dur, has_audio = 0.0, False
    try:
        j = paths.parse_probe_json(log)
        dur = float(j.get("format", {}).get("duration", 0.0))
        has_audio = any(s.get("codec_type") == "audio" for s in j.get("streams", []))
    except Exception:
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", log)
        if m:
            dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return dur, has_audio


# ----------------------------------------------------------------- audio
def audio_track(video: str, sr: int = 8000, hop: float = 0.1,
                audio_file: str | None = None) -> tuple[np.ndarray, dict]:
    """
    解码为单声道 PCM,自己算逐窗 RMS 能量。

    为什么不用 ebur128:framelog=verbose 的逐帧 M: 输出在 stderr 被重定向/缓冲时
    会被吞掉,结果不稳定。自己解码 PCM 反而更快、更可控,而且能顺带算峰值和削波。

    audio_file: 复用已抽好的 wav(转写也复用同一份),免得为了响度再解一次
                1080p60 的长视频。

    返回 (array[[t, dbfs], ...], meta)
    """
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-i", audio_file or video]
    if not audio_file:
        cmd.append("-vn")
    cmd += ["-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    x = np.frombuffer(proc.stdout, dtype=np.float32)
    if x.size == 0:
        return np.zeros((0, 2)), {}

    step = max(1, int(sr * hop))
    n = x.size // step
    if n == 0:
        return np.zeros((0, 2)), {}
    x = x[: n * step].reshape(n, step)

    # 分块算功率:6 小时的素材一次性 x**2 要 1.3 GB,分块后峰值只有 chunk*step
    mean_sq = np.empty(n, dtype=np.float64)
    peak = np.empty(n, dtype=np.float32)
    CHUNK = 20_000                     # 每次处理 2 万个窗口
    for i in range(0, n, CHUNK):
        blk = x[i:i + CHUNK]
        mean_sq[i:i + CHUNK] = np.einsum("ij,ij->i", blk, blk, dtype=np.float64) / step
        peak[i:i + CHUNK] = np.abs(blk).max(axis=1)

    rms = np.sqrt(mean_sq)
    eps = 1e-9
    dbfs = 20.0 * np.log10(np.maximum(rms, eps))
    times = np.arange(n) * hop

    overall_rms = float(np.sqrt(mean_sq.mean()))
    meta = {
        "sample_rate": sr,
        "hop": hop,
        "peak_dbfs": round(float(20 * np.log10(max(float(peak.max()), eps))), 2),
        "rms_dbfs": round(float(20 * np.log10(max(overall_rms, eps))), 2),
        "clipped_ratio": round(float((np.abs(x) > 0.999).mean()), 6),
        "silent_ratio_raw": round(float((dbfs < -45).mean()), 3),
    }
    return np.column_stack([times, dbfs]), meta


def silence_runs(video: str, noise_db: float = -35.0, min_dur: float = 0.6
                 ) -> list[tuple[float, float]]:
    log = _run([
        FFMPEG, "-hide_banner", "-nostats", "-i", video, "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-",
    ])
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", log)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else math.inf
        out.append((max(0.0, s), e))
    return out


# ----------------------------------------------------------------- video
CODEC_HW = {"hevc": "hevc_cuvid", "h264": "h264_cuvid",
            "av1": "av1_cuvid", "vp9": "vp9_cuvid", "mpeg4": "mpeg4_cuvid"}


def detect_vcodec(path: str) -> str:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=codec_name", "-of", "json", path],
                       capture_output=True, text=True, errors="replace")
    try:
        return paths.parse_probe_json(r.stdout)["streams"][0]["codec_name"]
    except Exception:
        return ""


def motion_track(video: str, fps: float, hw: bool = False
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    逐帧灰度差分 -> (times, motion 0~100, dark 0/1)。

    hw=True 时用 N 卡硬件解码(实测 hevc_cuvid 比软解快 2.6 倍)。
    注意别用 -hwaccel cuda:那个组合在这里会产出 0 帧。
    """
    cmd = [FFMPEG, "-hide_banner", "-nostats"]
    if hw:
        codec = detect_vcodec(video)
        dec = CODEC_HW.get(codec)
        if dec:
            cmd += ["-c:v", dec]
    cmd += [
        "-i", video, "-an",
        "-vf", f"fps={fps},scale={W}:{H}:flags=bilinear,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    buf = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = len(buf) // (W * H)
    if n < 2:
        return np.zeros((0,)), np.zeros((0,)), np.zeros((0,))
    frames = buf[: n * W * H].reshape(n, H, W).astype(np.float32)

    # 逐帧平均绝对差分
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    # 亮度均值 -> 黑场
    lum = frames.mean(axis=(1, 2))
    times = (np.arange(1, n) / fps)
    # 归一化到 0~100:用 95 分位做上界,避免个别闪光把整体压扁
    hi = float(np.percentile(diff, 95)) or 1.0
    motion = np.clip(diff / hi * 60.0, 0, 100)
    dark = (lum[1:] < 12.0).astype(np.float32)
    return times, motion, dark


# ----------------------------------------------------------------- merge
def bucketize(dur: float, win: float, au: np.ndarray, meta: dict,
              sils: list[tuple[float, float]], mt: np.ndarray,
              mo: np.ndarray, dk: np.ndarray) -> dict:
    n = max(1, int(math.ceil(dur / win)))
    loud = np.full(n, np.nan)
    motion = np.zeros(n)
    dark = np.zeros(n)
    cut = np.zeros(n, dtype=bool)
    silent = np.zeros(n, dtype=bool)

    for t, v in au:
        i = min(n - 1, int(t // win))
        loud[i] = np.nanmax([loud[i], v]) if not np.isnan(loud[i]) else v
    # 响度极低的窗口也算静音(与 silencedetect 互补)
    silent |= np.nan_to_num(loud, nan=0.0) < -45.0
    for i in range(len(mt)):
        b = min(n - 1, int(mt[i] // win))
        motion[b] = max(motion[b], mo[i])
    for i in range(len(mt)):
        b = min(n - 1, int(mt[i] // win))
        if dk[i] > 0:
            dark[b] = 1
    # 镜头切换:运动值显著高于邻域
    if len(mo) > 4:
        thr = float(np.percentile(mo, 99.0))
        for i in np.where(mo >= max(thr, 25.0))[0]:
            cut[min(n - 1, int(mt[i] // win))] = True
    for s, e in sils:
        hi_i = min(n - 1, int((min(e, dur) if math.isfinite(e) else dur) // win))
        silent[max(0, int(s // win)): hi_i + 1] = True

    windows = [
        {
            "t": round(i * win, 2),
            "loud": None if np.isnan(loud[i]) else round(float(loud[i]), 1),
            "motion": round(float(motion[i]), 1),
            "cut": bool(cut[i]),
            "silent": bool(silent[i]),
            "dark": bool(dark[i] > 0),
        }
        for i in range(n)
    ]
    return {
        "duration": round(dur, 3),
        "window": win,
        "audio_meta": meta,
        "silence_ratio": round(float(silent.mean()), 3),
        "dark_ratio": round(float(dark.mean()), 3),
        "cut_count": int(cut.sum()),
        "windows": windows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default=None)
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--fps", type=float, default=4.0)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--audio", default=None,
                    help="复用已抽好的 wav(转写也用同一份),"
                         "避免为响度/静音再解码一次长视频")
    ap.add_argument("--hw", action="store_true",
                    help="用 N 卡硬件解码(hevc_cuvid 等),实测快 2.6 倍")
    a = ap.parse_args()

    if not os.path.exists(a.video):
        print(f"文件不存在: {a.video}", file=sys.stderr)
        return 2

    out = a.out or os.path.splitext(a.video)[0] + ".metrics.json"
    t0 = time.time()
    dur, has_audio = probe_duration(a.video)
    print(f"[probe] 时长 {dur:.1f}s 音轨={has_audio}", file=sys.stderr, flush=True)

    au = np.zeros((0, 2)); meta = {}; sils = []
    if has_audio or a.audio:
        src = a.audio or a.video
        print(f"[audio] 解码 PCM + 逐窗 RMS ({os.path.basename(src)}) ...",
              file=sys.stderr, flush=True)
        au, meta = audio_track(a.video, hop=0.25, audio_file=a.audio)
        sils = silence_runs(src)
        print(f"[audio] 能量窗口 {len(au)} 静音段 {len(sils)}", file=sys.stderr, flush=True)

    if a.no_video:
        mt = np.zeros(0); mo = np.zeros(0); dk = np.zeros(0)
    else:
        print(f"[video] 逐帧差分 @{a.fps}fps {W}x{H} "
              f"{'硬解' if a.hw else '软解'} ...", file=sys.stderr, flush=True)
        mt, mo, dk = motion_track(a.video, a.fps, hw=a.hw)
        print(f"[video] 帧数 {len(mt)+1}", file=sys.stderr, flush=True)

    summ = bucketize(dur, a.window, au, meta, sils, mt, mo, dk)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summ, f, ensure_ascii=False)

    print(f"\n总耗时 {time.time()-t0:.1f}s -> {out}\n", file=sys.stderr)
    print(json.dumps({k: v for k, v in summ.items() if k != "windows"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
