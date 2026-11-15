"""
shots.py —— 镜头边界检测 + 把切点吸附到镜头边界(对齐 PySceneDetect 的 ContentDetector 思路)

★ 为什么需要(2026-11 影之刃实战):
  即使节拍长度对了,切点仍可能落在**一个镜头中间**(动作挥到一半/话说一半),
  观众会觉得"突兀地跳了一下"。影视剪辑的通行做法是**切在镜头切换处**(cut on cut):
  镜头本身有切换,观众的心理预期就是"这里可以跳"。

  PySceneDetect 的 ContentDetector 本质是"相邻帧在 HSV 空间的变化加权和超过阈值即切镜头"。
  本机装不上该包(PyPI 不通),这里按其算法自己实现:
    逐个采样帧 -> 计算 HSV 直方图差 + 边缘差 -> 自适应阈值(mean+k*std)取局部峰

★ 成本控制:全片检测要再解码一遍(45.8h 约 25 分钟)。但我们只需要**切点附近**的镜头边界,
  所以只解码每个切点 ±--snap 秒的小窗口(实测 142 个切点 × 12 秒 = 约 1 分钟)。

用法:
  python shots.py --cuts beats.cuts.json --src-dir "G:\\录像\\影之刃" \
         --out beats.snapped.cuts.json [--snap 6] [--fps 8]
  python shots.py --probe video.mkv --start 600 --dur 60      # 单窗口调试
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

FF = paths.ffmpeg()
W, H = 192, 108


def decode_window(video: str, t0: float, dur: float, fps: float = 8.0) -> np.ndarray:
    """解码 [t0,t0+dur) 为小尺寸 BGR 帧序列,返回 (n,H,W,3)。"""
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.2f}",
           "-t", f"{dur:.2f}", "-i", video, "-an",
           "-vf", f"fps={fps},scale={W}:{H}:flags=bilinear",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    r = subprocess.run(cmd, capture_output=True)
    buf = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(buf) // (W * H * 3)
    if n <= 0:
        return np.zeros((0, H, W, 3), dtype=np.uint8)
    return buf[: n * W * H * 3].reshape(n, H, W, 3)


def content_scores(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """相邻帧的 HSV 直方图差 + 边缘差,归一化后返回内容变化分。"""
    if len(frames) < 2:
        return np.zeros(0), np.zeros(0)
    hsv = [cv2.cvtColor(f, cv2.COLOR_BGR2HSV) for f in frames]
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    hist_d = np.zeros(len(frames) - 1)
    edge_d = np.zeros(len(frames) - 1)
    for i in range(len(frames) - 1):
        h1 = cv2.calcHist([hsv[i]], [0, 1], None, [32, 32], [0, 180, 0, 256])
        h2 = cv2.calcHist([hsv[i + 1]], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(h1, h1)
        cv2.normalize(h2, h2)
        hist_d[i] = float(np.abs(h1 - h2).sum())
        e1 = cv2.Laplacian(gray[i], cv2.CV_32F)
        e2 = cv2.Laplacian(gray[i + 1], cv2.CV_32F)
        edge_d[i] = float(np.abs(np.abs(e1) - np.abs(e2)).mean())
    # 各自按 p95 归一,再加权(颜色主导,边缘辅助)
    h95 = np.percentile(hist_d, 95) or 1.0
    e95 = np.percentile(edge_d, 95) or 1.0
    score = 0.75 * (hist_d / h95) + 0.25 * (edge_d / e95)
    return score, hist_d


def detect_cuts(video: str, t0: float, dur: float, fps: float = 8.0,
                k: float = 1.8) -> list[float]:
    """在窗口内找镜头切换(局部峰 + 自适应阈值),返回绝对时间秒。"""
    frames = decode_window(video, t0, dur, fps)
    if len(frames) < 3:
        return []
    score, _ = content_scores(frames)
    if not len(score):
        return []
    # 长窗口:滑动窗口内自适应(镜头切换密度不同)
    cuts: list[float] = []
    win = int(max(16, min(len(score), fps * 30)))
    for i in range(len(score)):
        lo = max(0, i - win // 2)
        hi = min(len(score), i + win // 2 + 1)
        seg = score[lo:hi]
        mu, sd = float(seg.mean()), float(seg.std())
        thr = mu + k * sd
        if score[i] < thr or score[i] < 0.5:
            continue
        # 局部最大
        if i > 0 and score[i] < score[i - 1]:
            continue
        if i + 1 < len(score) and score[i] < score[i + 1]:
            continue
        cuts.append(t0 + (i + 1) / fps)
    # 合并过近的切点(镜头最短 0.4s)
    out: list[float] = []
    for c in cuts:
        if not out or c - out[-1] >= 0.4:
            out.append(c)
    return out


def snap_edge(video: str, t: float, snap: float, fps: float,
              kind: str) -> tuple[float, float]:
    """把切点 t 吸附到 ±snap 内最近的镜头边界;返回 (新时间, 位移)。"""
    t0 = max(0.0, t - snap)
    cuts = detect_cuts(video, t0, snap * 2, fps)
    if not cuts:
        return t, 0.0
    best = min(cuts, key=lambda c: abs(c - t))
    if abs(best - t) > snap * 0.9:
        return t, 0.0
    return round(best, 2), round(best - t, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuts")
    ap.add_argument("--src-dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--snap", type=float, default=6.0)
    ap.add_argument("--fps", type=float, default=8.0)
    ap.add_argument("--probe")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=60.0)
    a = ap.parse_args()

    if a.probe:
        cuts = detect_cuts(a.probe, a.start, a.dur, a.fps)
        print(f"{os.path.basename(a.probe)} {a.start:.0f}s+{a.dur:.0f}s -> "
              f"{len(cuts)} 个镜头切换")
        for c in cuts[:20]:
            print(f"  {int(c)//60:>3}:{c % 60:05.2f}")
        return 0

    if not (a.cuts and a.src_dir):
        print("需要 --cuts 和 --src-dir(或 --probe)", file=sys.stderr)
        return 2

    d = json.load(open(a.cuts, encoding="utf-8"))
    cuts = d["cuts"]
    videos = {os.path.splitext(f)[0]: os.path.join(a.src_dir, f)
              for f in os.listdir(a.src_dir) if f.lower().endswith((".mkv", ".mp4"))}
    n_edge = n_snap = 0
    shifts = []
    overlaps_fixed = 0
    # ★ 必须按「同一素材内的时间序列」逐一约束吸附:
    #   早期版本对每个切点独立吸附,导致后一段起点被吸到前一段终点之前 -> 片段重叠
    #   (实测 EP01 有两段重叠 55 秒)。这里保证 start>=prev_end、end>=start+1。
    by_src: dict[str, list[dict]] = {}
    for c in cuts:
        by_src.setdefault(c.get("src"), []).append(c)
    for src, group in by_src.items():
        video = videos.get(src)
        if not video or not os.path.exists(video):
            continue
        group.sort(key=lambda x: float(x["start"]))
        prev_end = -1.0
        for c in group:
            orig_s, orig_e = float(c["start"]), float(c["end"])
            n_edge += 1
            s_new, d1 = snap_edge(video, orig_s, a.snap, a.fps, "start")
            # 不能越过前一段终点
            if s_new <= prev_end + 0.2 or s_new >= orig_e - 0.5:
                s_new = max(orig_s, prev_end + 0.2) if prev_end > 0 else orig_s
                d1 = 0.0
            n_edge += 1
            e_new, d2 = snap_edge(video, orig_e, a.snap, a.fps, "end")
            if e_new <= s_new + 1.0:
                e_new, d2 = orig_e, 0.0
            for delta in (d1, d2):
                if abs(delta) > 0.01:
                    n_snap += 1
                    shifts.append(abs(delta))
            if s_new < prev_end - 0.01:
                overlaps_fixed += 1
                s_new = prev_end
            c["start"], c["end"] = round(s_new, 2), round(e_new, 2)
            c["dur"] = round(c["end"] - c["start"], 2)
            prev_end = c["end"]
    # 兜底:按"吸附后的最终起点"再扫一遍重叠。
    # 为什么需要:吸附可能把某段起点吸到更早的镜头切点,导致它排到前一段之前,
    # 单靠"按原始顺序"的约束管不住(实测仍会剩 1~2 处重叠)。
    for src, group in by_src.items():
        group.sort(key=lambda x: float(x["start"]))
        for i in range(1, len(group)):
            if group[i]["start"] < group[i - 1]["end"]:
                group[i]["start"] = group[i - 1]["end"]
                group[i]["dur"] = round(group[i]["end"] - group[i]["start"], 2)
                overlaps_fixed += 1
        cuts = [c for c in cuts if not (c.get("src") == src and c["dur"] <= 1.0)]

    # 兜底:清理异常短/反序片段
    before = len(cuts)
    cuts = [c for c in cuts if c["dur"] > 1.0]
    removed = before - len(cuts)

    print(f"切点 {n_edge} 个 -> 吸附 {n_snap} 个 "
          f"({n_snap / max(n_edge, 1) * 100:.0f}%)  "
          f"平均位移 {np.mean(shifts) if shifts else 0:.2f}s  "
          f"最大 {max(shifts) if shifts else 0:.2f}s")
    if overlaps_fixed:
        print(f"  按序列约束修正重叠 {overlaps_fixed} 处")
    if removed:
        print(f"  吸附后丢弃 {removed} 个过短片段")
    # 重叠校验
    bad = 0
    for src, group in by_src.items():
        group.sort(key=lambda x: float(x["start"]))
        for i in range(1, len(group)):
            if group[i]["start"] < group[i - 1]["end"] - 0.01:
                bad += 1
    print(f"  重叠检查: {'通过(0 处重叠)' if bad == 0 else f'{bad} 处仍重叠 ❌'}")

    op = a.out or os.path.splitext(a.cuts)[0] + ".snapped.cuts.json"
    d["cuts"] = cuts
    d["snap"] = {"snap_win": a.snap, "fps": a.fps,
                 "edges": n_edge, "snapped": n_snap,
                 "mean_shift": round(float(np.mean(shifts)), 3) if shifts else 0.0}
    json.dump(d, open(op, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
