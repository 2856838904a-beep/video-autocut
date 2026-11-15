"""
subtitle_ocr.py —— 画面内字幕抽取(把"剧情对白"变成可靠的一等信号)

★ 为什么需要它(2026-11 影之刃实测):

  动作游戏的台词只出现在过场/对话里,而 whisper 在**安静的剧情对白**上错误率很高:
    字幕原文: 所谓正邪,不过是庸人自扰
    whisper : 所谓政协,不过是拥人自扰
  更要命的是:这种密集剧情段**没有战斗张力**,词典匹配又被错字拖低,
  于是在"按分数选高光"里根本进不了候选 —— 实测一段 78 秒连续对白
  (55:49–57:59)没有进任何一集。

  但这类游戏的**字幕是画在画面上的**,OCR 出来的文本干净、带精确时间,
  可以同时解决三件事:
    1. 剧情对白的可靠分段(字幕连续出现 = 一段对话;字幕消失 = 场景结束)
    2. 干净文本 -> 叙事/情绪词典打分才准
    3. (可选)生成字幕素材

★ 成本控制:全片 1fps OCR 要几小时。这里先用 whisper 句子密度圈出
  「疑似对白区间」,只对这些区间 OCR(默认 0.5fps —— 字幕通常持续 >=2 秒),
  实测只占全片 15~30%,把 OCR 成本压到十几分钟量级。

用法:
  python subtitle_ocr.py --src video.mkv --transcript t.json \
         --fps 0.5 --out subs.json [--band 0.70 0.99] [--pad 12]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
import transcript as transcript_mod

FF = paths.ffmpeg()


def read_img(p: str):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


def candidate_windows(segs: list[dict], total: float, win: float = 20.0,
                      cover: float = 0.35, pad: float = 12.0) -> list[tuple[float, float]]:
    """用 whisper 句子密度圈出疑似对白区间(不依赖文本质量,只看"有没有人在说话")。"""
    n = max(1, int(total // win) + 1)
    talked = [0.0] * n
    for s in segs:
        i0, i1 = int(s["start"] // win), int(s["end"] // win)
        for i in range(max(0, i0), min(n, i1 + 1)):
            talked[i] += 1
    hot = [i for i in range(n) if talked[i] > 0]
    if not hot:
        return []
    # 连续 hot 窗口合并(允许中间空 2 个窗口)
    runs, cur = [], [hot[0], hot[0]]
    for i in hot[1:]:
        if i - cur[1] <= 2:
            cur[1] = i
        else:
            runs.append(cur)
            cur = [i, i]
    runs.append(cur)
    out = []
    for a, b in runs:
        s0 = max(0.0, a * win - pad)
        s1 = min(total, (b + 1) * win + pad)
        if s1 - s0 >= 20:
            out.append((round(s0, 1), round(s1, 1)))
    return out


def ocr_span(src: str, t0: float, t1: float, fps: float, band: tuple[float, float],
             tmp: str) -> list[dict]:
    """对 [t0,t1) 抽「字幕带」帧并 OCR。

    ★ 性能关键(实测):早期版本整帧落 PNG 再读回,1920x1080 单帧约 2MB,
      实测 2.2 秒/帧、全量要 4.6 小时。现在先用 ffmpeg 裁出字幕带并缩到 800 宽,
      单帧只有几十 KB,PNG 编解码开销基本消失。
    """
    os.makedirs(tmp, exist_ok=True)
    for f in os.listdir(tmp):
        try:
            os.remove(os.path.join(tmp, f))
        except OSError:
            pass
    h0, h1 = band
    vf = (f"crop=iw:ih*{h1 - h0:.4f}:0:ih*{h0:.4f},scale=800:-2,fps={fps}")
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{t0:.1f}", "-t", f"{t1 - t0:.1f}", "-i", src,
           "-an", "-vf", vf, os.path.join(tmp, "f%05d.png")]
    subprocess.run(cmd, capture_output=True)
    files = sorted(os.path.join(tmp, f) for f in os.listdir(tmp))
    out: list[dict] = []
    for i, f in enumerate(files):
        img = read_img(f)
        if img is None:
            continue
        try:
            res, _ = OCR(img)
        except Exception:
            res = None
        t = t0 + i / fps
        lines = []
        for box, txt, score in (res or []):
            xs = [p[0] for p in box]
            cx = (min(xs) + max(xs)) / 2
            cy = (min(p[1] for p in box) + max(p[1] for p in box)) / 2
            if float(score) > 0.45 and abs(cx - img.shape[1] / 2) < img.shape[1] * 0.34 \
                    and len(txt) >= 4:
                lines.append((txt.strip(), float(score), cy))
        if lines:
            lines.sort(key=lambda x: x[2])
            out.append({"t": round(t, 2), "text": " ".join(l[0] for l in lines)})
    return out


def to_blocks(frames: list[dict], gap_merge: float = 6.0,
              gap_scene: float = 20.0) -> list[dict]:
    """字幕帧序列 -> 对白段。

    gap_merge:帧间隔小于它视为同一句话的连续出现
    gap_scene:字幕消失超过它 = 场景切换,断开
    """
    blocks: list[dict] = []
    cur = None
    for f in frames:
        if cur is None:
            cur = {"start": f["t"], "end": f["t"], "texts": [f["text"]]}
            continue
        gap = f["t"] - cur["end"]
        same = (f["text"] == cur["texts"][-1])
        if gap <= gap_merge:
            cur["end"] = f["t"]
            if not same:
                cur["texts"].append(f["text"])
        else:
            blocks.append(cur)
            if gap <= gap_scene and blocks:
                pass
            cur = {"start": f["t"], "end": f["t"], "texts": [f["text"]]}
    if cur:
        blocks.append(cur)
    # 合并:间隔 <= gap_merge 的相邻段(同一场景里的连续对话)
    merged: list[dict] = []
    for b in blocks:
        if merged and b["start"] - merged[-1]["end"] <= gap_merge:
            m = merged[-1]
            m["end"] = max(m["end"], b["end"])
            for t in b["texts"]:
                if t not in m["texts"]:
                    m["texts"].append(t)
        else:
            merged.append(b)
    out = []
    for m in merged:
        dur = m["end"] - m["start"] + 1.0
        if dur < 3:
            continue
        out.append({"start": round(m["start"], 2),
                    "end": round(m["end"] + 1.0, 2),
                    "dur": round(dur, 2),
                    "text": " / ".join(m["texts"])[:600],
                    "lines": len(m["texts"])})
    return out


OCR = None


def main() -> int:
    global OCR
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--fps", type=float, default=0.5)
    ap.add_argument("--band", type=float, nargs=2, default=[0.70, 0.99])
    ap.add_argument("--pad", type=float, default=12.0)
    ap.add_argument("--max-window", type=float, default=240.0,
                    help="单个 OCR 区间最长秒数(过长会切片,避免抽帧太多)")
    a = ap.parse_args()

    from rapidocr_onnxruntime import RapidOCR
    OCR = RapidOCR()

    tr, segs = transcript_mod.load(a.transcript, verbose=False)
    total = float(tr.get("meta", {}).get("duration_sec") or 0.0)
    if not total:
        # 从 metrics 或最后一个句子推
        total = max((s["end"] for s in segs), default=0.0) + 60
    wins = candidate_windows(segs, total, pad=a.pad)
    covered = sum(b - a_ for a_, b in wins)
    print(f"台词 {len(segs)} 句   疑似对白区间 {len(wins)} 个 / "
          f"{covered/60:.1f} 分钟(占素材 {covered/max(total,1)*100:.0f}%)")

    tmp = os.path.join(os.path.dirname(os.path.abspath(a.out or a.src)), "_ocr_tmp")
    t_start = time.time()
    frames: list[dict] = []
    for i, (t0, t1) in enumerate(wins, 1):
        span = t1 - t0
        chunks = max(1, int(np.ceil(span / a.max_window)))
        for k in range(chunks):
            s0 = t0 + k * a.max_window
            s1 = min(t1, s0 + a.max_window)
            if s1 - s0 < 8:
                continue
            got = ocr_span(a.src, s0, s1, a.fps, tuple(a.band), tmp)
            frames.extend(got)
        el = time.time() - t_start
        print(f"  [{i}/{len(wins)}] {t0/60:6.1f}–{t1/60:6.1f} 分  "
              f"字幕帧 {len(frames):>5}  已用 {el/60:.1f} 分", flush=True)

    frames.sort(key=lambda f: f["t"])
    blocks = to_blocks(frames)
    print(f"\n字幕帧 {len(frames)} / 对白段 {len(blocks)}  "
          f"总时长 {sum(b['dur'] for b in blocks)/60:.1f} 分钟  "
          f"OCR 耗时 {(time.time()-t_start)/60:.1f} 分")

    op = a.out or os.path.splitext(a.src)[0] + ".subs.json"
    json.dump({"src": os.path.basename(a.src), "fps": a.fps,
               "band": a.band, "candidate_windows": wins,
               "frames": frames, "blocks": blocks},
              open(op, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"-> {op}")
    for b in blocks[:12]:
        print(f"  {int(b['start'])//60:>3}:{int(b['start'])%60:02d}–"
              f"{int(b['end'])//60:>3}:{int(b['end'])%60:02d} "
              f"({b['dur']:6.1f}s) {b['text'][:56]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
