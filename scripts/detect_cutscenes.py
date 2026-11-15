r"""
detect_cutscenes.py —— 检测过场动画区间(单机 3A 故事剪辑的核心信号)

为什么需要专门的检测器:
  单机 3A 的"剧情价值"不在解说密度,而在剧情演出本身。而剧情演出有稳定的
  画面特征 —— **电影黑边**(上下黑条)。实测大镖客:
     正常游玩  满幅 1080
     过场动画  有效高 816(上下各 ~132 黑边)

★ 为什么不用 ffmpeg 的 cropdetect(踩过的坑):
  1. cropdetect 在检测值稳定后会**静默**(不再逐帧打印),除非每帧强制 reset。
     实测 45 分钟素材只拿到 28 行,而不是预期的 2700 行。
  2. 它的日志里同时有 `pts:N` 和 `t:X` 两个数字字段,用 t:([\d.]+) 之类
     的正则很容易误匹配 pts(实测就踩了,导致时间戳全错)。
  所以这里改为:自己解码灰度帧,直接测上下边缘的暗度 —— 更快更准。

用法:
  python detect_cutscenes.py --src <视频> [--fps 1] [--min 8] [--out x.cuts.json]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

import paths

FFMPEG = paths.ffmpeg()
FFPROBE = paths.ffprobe()
W, H = 320, 180          # 分析用缩略尺寸,黑边检测足够
DARK = 32                # 亮度低于此值视为黑边像素


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 60:02d}:{t % 60:02d}"


def probe_wh(src: str) -> tuple[int, int, float]:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-show_entries", "format=duration", "-of", "json", src],
                       capture_output=True, text=True, errors="replace")
    try:
        j = json.loads(r.stdout)
        st = j["streams"][0]
        return int(st["width"]), int(st["height"]), float(j["format"]["duration"])
    except Exception:
        return 0, 0, 0.0


def decode_gray(src: str, fps: float) -> np.ndarray:
    """解成 (n, H, W) 的灰度帧。"""
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-i", src, "-an",
           "-vf", f"fps={fps},scale={W}:{H}:flags=fast_bilinear,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = subprocess.run(cmd, capture_output=True)
    b = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(b) // (W * H)
    if n == 0:
        return np.zeros((0, H, W), dtype=np.uint8)
    return b[: n * H * W].reshape(n, H, W)


def bar_rows(frame: np.ndarray, dark: int = DARK, row_frac: float = 0.98) -> tuple[int, int]:
    """返回 (顶部黑行数, 底部黑行数)。一行里 >=row_frac 的像素是暗的,就算黑行。"""
    rows_dark = (frame < dark).mean(axis=1) >= row_frac
    top = 0
    for v in rows_dark:
        if v:
            top += 1
        else:
            break
    bot = 0
    for v in rows_dark[::-1]:
        if v:
            bot += 1
        else:
            break
    return top, bot


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--min", dest="min_dur", type=float, default=8.0)
    ap.add_argument("--max", dest="max_dur", type=float, default=0.0)
    ap.add_argument("--min-bar-px", type=int, default=40,
                    help="换算到原始分辨率后的黑边高度阈值(像素)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    if not os.path.isfile(src):
        print(f"文件不存在: {src}", file=sys.stderr)
        return 2

    full_w, full_h, dur = probe_wh(src)
    if not full_h:
        print("读不到分辨率", file=sys.stderr)
        return 2
    print(f"素材 {os.path.basename(src)}   {full_w}x{full_h}   {dur / 60:.1f} 分钟")
    print(f"采样 {a.fps}fps -> {W}x{H} 灰度,黑边阈值 {a.min_bar_px}px(原始尺度)")

    frames = decode_gray(src, a.fps)
    if len(frames) == 0:
        print("解码失败", file=sys.stderr)
        return 2
    print(f"解码 {len(frames)} 帧")

    # 逐帧测黑边(换算回原始像素)
    scale = full_h / H
    top_px = np.zeros(len(frames), dtype=int)
    bot_px = np.zeros(len(frames), dtype=int)
    for i in range(len(frames)):
        t, b = bar_rows(frames[i])
        top_px[i] = int(t * scale)
        bot_px[i] = int(b * scale)

    has_bar = (np.minimum(top_px, bot_px) >= a.min_bar_px)
    print(f"带黑边的帧 {has_bar.sum()} / {len(frames)} ({has_bar.mean() * 100:.1f}%)")
    if has_bar.any():
        idx = np.where(has_bar)[0]
        print(f"  黑边典型值: 上 {int(np.median(top_px[idx]))}px  "
              f"下 {int(np.median(bot_px[idx]))}px")

    # 聚合成区间(允许中间短暂跳出)
    step = 1.0 / a.fps
    segs: list[list[int]] = []
    cur: list[int] | None = None
    gap_tol = 2
    for i, f in enumerate(has_bar):
        if f:
            if cur is None:
                cur = [i, i]
            else:
                cur[1] = i
        elif cur is not None:
            nxt = np.where(has_bar[i + 1:i + 1 + gap_tol])[0]
            if len(nxt):
                continue
            segs.append(cur)
            cur = None
    if cur:
        segs.append(cur)

    merged: list[dict] = []
    for i0, i1 in segs:
        s0, s1 = i0 * step, (i1 + 1) * step
        d = s1 - s0
        if d < a.min_dur:
            continue
        if a.max_dur and d > a.max_dur:
            k = int(d // a.max_dur) + (1 if d % a.max_dur else 0)
            for j in range(k):
                p0 = s0 + j * a.max_dur
                p1 = min(s1, p0 + a.max_dur)
                if p1 - p0 >= a.min_dur:
                    merged.append({"start": round(p0, 1), "end": round(p1, 1),
                                   "dur": round(p1 - p0, 1)})
        else:
            merged.append({"start": round(s0, 1), "end": round(s1, 1), "dur": round(d, 1)})

    total_bar = sum(m["dur"] for m in merged)
    print(f"\n过场区间 {len(merged)} 段,共 {total_bar / 60:.1f} 分钟 "
          f"(占素材 {total_bar / max(dur, 1e-6) * 100:.1f}%)")
    print("=" * 72)
    for i, m in enumerate(merged, 1):
        print(f"  #{i:>2}  {fmt(m['start'])}–{fmt(m['end'])}  {m['dur']:6.1f}s")
        if i >= 45:
            print(f"  ... 还有 {len(merged) - 45} 段")
            break

    op = a.out or os.path.splitext(src)[0] + ".cutscenes.json"
    with open(op, "w", encoding="utf-8") as f:
        json.dump({"genre": "cutscene", "source": src, "full": [full_w, full_h],
                   "duration": dur,
                   "cuts": [{"start": m["start"], "end": m["end"], "dur": m["dur"],
                             "score": round(m["dur"], 1),
                             "dims": {"过场演出": 1}, "text": ""} for m in merged]},
                  f, ensure_ascii=False, indent=1)
    print(f"\n-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
