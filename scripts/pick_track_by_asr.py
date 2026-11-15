"""
pick_track_by_asr.py —— 用「转写质量」挑解说轨(替代纯能量判据)

为什么要换判据:
  纯能量(>-35dB 时间占比)只能告诉你"这条轨有声音",不能告诉你"有解说"。
  游戏音效/音乐可能比人声更响 —— 实测 6 个文件因此挑错轨,最极端的
  2 小时素材只转出 1 句。
  真正的判据是:把这条轨送去转写,看能不能转出成句的中文。

做法:
  1. 按能量活跃度筛出候选轨(top N)
  2. 每条候选轨取几段各 90 秒的样本,用 faster-whisper 快速转写
  3. 按「中文字符数 / 样本秒数」打分 —— 人声轨会明显胜出
  4. 输出每条轨的分数,挑最高的

用法:
  python pick_track_by_asr.py --src <素材> [--candidates 3] [--seconds 90]
                              [--out track_by_asr.json]
"""
from __future__ import annotations

import argparse
import json
import os
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
CJK = __import__("re").compile(r"[\u4e00-\u9fff]")


def audio_indices(path: str) -> list[int]:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "json", path],
                       capture_output=True, text=True, errors="replace")
    try:
        return [s["index"] for s in paths.parse_probe_json(r.stdout)["streams"]]
    except Exception:
        return []


def duration_of(path: str) -> float:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", path], capture_output=True, text=True, errors="replace")
    try:
        return float(paths.parse_probe_json(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def energy_activity(path: str, aidx: int, dur: float) -> float:
    """粗筛用:10 点采样取最大活跃度。"""
    act = []
    for f in [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]:
        cmd = [FFMPEG, "-hide_banner", "-nostats", "-ss", f"{max(0.0, dur * f):.1f}",
               "-t", "20", "-i", path, "-map", f"0:{aidx}", "-vn",
               "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
        r = subprocess.run(cmd, capture_output=True)
        x = np.frombuffer(r.stdout, dtype=np.float32)
        if x.size < 6400:
            continue
        step = 3200
        n = x.size // step
        y = x[: n * step].reshape(n, step)
        ms = np.einsum("ij,ij->i", y, y, dtype=np.float64) / step
        db = 20 * np.log10(np.maximum(np.sqrt(ms), 1e-9))
        act.append(float((db > -35).mean()))
    return float(max(act)) if act else 0.0


def sample_wav(path: str, aidx: int, dur: float, seconds: float, dst: str) -> float:
    """抽几段拼成一个样本 wav,返回总时长。"""
    picks = [0.15, 0.45, 0.75] if dur > seconds * 6 else [0.2, 0.6]
    tmpdir = os.path.dirname(dst)
    parts = []
    for k, f in enumerate(picks):
        st = max(0.0, dur * f)
        pp = os.path.join(tmpdir, f"p{aidx}_{k}.wav")
        subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{st:.1f}", "-t", f"{seconds:.1f}", "-i", path,
                        "-map", f"0:{aidx}", "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", pp], capture_output=True)
        if os.path.exists(pp) and os.path.getsize(pp) > 10000:
            parts.append(pp)
    if not parts:
        return 0.0
    lst = os.path.join(tmpdir, f"list{aidx}.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")
    r = subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "concat", "-safe", "0", "-i", lst,
                        "-c", "copy", dst], capture_output=True)
    for p in parts:
        try:
            os.remove(p)
        except OSError:
            pass
    try:
        os.remove(lst)
    except OSError:
        pass
    if not os.path.exists(dst):
        return 0.0
    r2 = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                         "-of", "json", dst], capture_output=True, text=True, errors="replace")
    try:
        return float(json.loads(r2.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--candidates", type=int, default=3, help="按能量筛前几条轨去试转写")
    ap.add_argument("--seconds", type=float, default=90, help="每条轨每段样本秒数")
    ap.add_argument("--model", default="small")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    if not os.path.isfile(src):
        print(f"文件不存在: {src}", file=sys.stderr)
        return 2

    dur = duration_of(src)
    idxs = audio_indices(src)
    print(f"素材 {os.path.basename(src)}  时长 {dur / 3600:.2f}h  音轨 {len(idxs)} 条")

    # 1) 能量粗筛
    print("\n[1/2] 能量粗筛(10 点采样取最大)")
    energy = {}
    for ai in idxs:
        e = energy_activity(src, ai, dur)
        energy[ai] = e
        print(f"   轨{ai}: {e * 100:5.1f}%")
    ranked = sorted(energy, key=lambda k: -energy[k])
    cands = [t for t in ranked if energy[t] > 0.05][: a.candidates]
    if not cands:
        cands = ranked[:1]
    print(f"   -> 候选轨(去转写验证): {cands}")

    # 2) 转写验证
    print(f"\n[2/2] 转写验证(每条轨抽 {a.seconds:.0f}s × 数段)")
    from faster_whisper import WhisperModel
    try:
        import torch
        if a.device == "cuda" and not torch.cuda.is_available():
            a.device = "cpu"
    except Exception:
        a.device = "cpu"
    ct = "float16" if a.device == "cuda" else "int8"
    t0 = time.time()
    model = WhisperModel(a.model, device=a.device, compute_type=ct)
    print(f"   模型就绪 ({time.time() - t0:.1f}s)")

    tmpdir = tempfile.mkdtemp(prefix="trackpick_")
    scores = {}
    for ai in cands:
        wav = os.path.join(tmpdir, f"s{ai}.wav")
        sec = sample_wav(src, ai, dur, a.seconds, wav)
        if sec < 5:
            scores[ai] = {"chars_per_min": 0.0, "note": "样本抽取失败"}
            print(f"   轨{ai}: 样本抽取失败")
            continue
        segs, info = model.transcribe(wav, language=None, beam_size=5,
                                      vad_filter=True, condition_on_previous_text=False)
        text = "".join(s.text for s in segs)
        cjk = len(CJK.findall(text))
        cpm = cjk / (sec / 60.0)
        scores[ai] = {"chars_per_min": round(cpm, 1), "cjk": cjk,
                      "sample_sec": round(sec, 1), "lang": info.language}
        print(f"   轨{ai}: 样本 {sec:.0f}s  语言 {info.language}  "
              f"中文字 {cjk}  = {cpm:6.1f} 字/分钟   | {text[:44]}")

    best = max(scores, key=lambda k: scores[k].get("chars_per_min", 0)) if scores else None
    print(f"\n   -> 判定解说轨: 轨{best}")
    if best is not None:
        e_best = energy.get(best, 0)
        e_max = max(energy.values()) if energy else 0
        print(f"      (能量 {e_best * 100:.0f}%,最高的轨是 "
              f"{max(energy, key=lambda k: energy[k])}({e_max * 100:.0f}%) "
              f"—— {'一致' if e_best == e_max else '不一致,能量判据在此文件失效'})")

    res = {"file": src, "duration": dur, "energy": {str(k): v for k, v in energy.items()},
           "scores": {str(k): v for k, v in scores.items()}, "best_track": best}
    op = a.out or (os.path.splitext(src)[0] + ".track_by_asr.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
