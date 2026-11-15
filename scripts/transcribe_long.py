"""
transcribe_long.py —— 长素材分块转写(解决 6 小时音频一次性转写 MemoryError)

为什么需要:对整条 6 小时音轨直接调 faster-whisper,VAD + 词级对齐会在内存里
堆爆(实测 MemoryError;系统 47 GB 内存、GPU 仅剩 3.8 GB)。
切成 20 分钟块后,内存占用恒定,而且模型只加载一次、可跨块复用。

附带好处:每块转写完立刻落盘,中断了也能断点续跑。

用法:
  python transcribe_long.py <audio_or_video> [--out x.json] [--chunk-min 20]
                            [--model small] [--device cuda]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

FFMPEG = paths.ffmpeg()


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def duration_of(p: str) -> float:
    r = subprocess.run([FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"),
                        "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", p],
                       capture_output=True, text=True, errors="replace")
    try:
        return float(paths.parse_probe_json(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def cut_chunk(src: str, start: float, dur: float, dst: str) -> bool:
    r = subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst],
        capture_output=True, text=True, errors="replace",
    )
    return r.returncode == 0 and os.path.exists(dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default=None)
    ap.add_argument("--chunk-min", type=float, default=20.0)
    ap.add_argument("--model", default="small")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--compute-type", default=None)
    ap.add_argument("--language", default=None)
    ap.add_argument("--vad", action="store_true", default=True)
    a = ap.parse_args()

    src = str(Path(a.src).expanduser().resolve())
    if not os.path.isfile(src):
        log(f"error: 文件不存在 {src}")
        return 2

    out_path = a.out or (os.path.splitext(src)[0] + "_transcript.json")
    cache_dir = out_path + ".chunks"
    os.makedirs(cache_dir, exist_ok=True)

    total = duration_of(src)
    if total <= 0:
        log("error: 读不出时长")
        return 2
    chunk_sec = a.chunk_min * 60
    n_chunks = int(total // chunk_sec) + (1 if total % chunk_sec else 0)
    log(f"[src] {os.path.basename(src)}  时长 {total / 3600:.2f} 小时  "
        f"切 {n_chunks} 块 ({a.chunk_min:.0f} 分钟/块)")

    # 自动降级
    try:
        import torch
        if a.device == "cuda" and not torch.cuda.is_available():
            log("[warn] CUDA 不可用,退回 cpu")
            a.device = "cpu"
    except Exception:
        if a.device == "cuda":
            log("[warn] torch 不可用,退回 cpu")
            a.device = "cpu"

    from faster_whisper import WhisperModel
    ct = a.compute_type or ("float16" if a.device == "cuda" else "int8")
    log(f"[model] 加载 {a.model} on {a.device} ({ct}) ...")
    t_load = time.time()
    model = WhisperModel(a.model, device=a.device, compute_type=ct)
    log(f"[model] 就绪,耗时 {time.time() - t_load:.1f}s")

    all_segs: list[dict] = []
    total_words = 0
    langs: list[str] = []
    infer_total = 0.0

    for ci in range(n_chunks):
        cs = ci * chunk_sec
        cd = min(chunk_sec, total - cs)
        if cd <= 1:
            break
        cpath = os.path.join(cache_dir, f"c{ci:04d}.wav")
        if not os.path.exists(cpath) or os.path.getsize(cpath) < 10000:
            if not cut_chunk(src, cs, cd, cpath):
                log(f"  [块{ci + 1}] 切块失败,跳过")
                continue

        cjson = os.path.join(cache_dir, f"c{ci:04d}.json")
        if os.path.exists(cjson):
            part = json.load(open(cjson, encoding="utf-8"))
            log(f"  [块{ci + 1}/{n_chunks}] 命中缓存 {len(part['segments'])} 句")
        else:
            t0 = time.time()
            seg_iter, info = model.transcribe(
                cpath, language=a.language, word_timestamps=True, beam_size=5,
                vad_filter=a.vad, condition_on_previous_text=False,
            )
            part_segs = []
            for s in seg_iter:
                part_segs.append({
                    "start": round(s.start, 3), "end": round(s.end, 3),
                    "text": s.text.strip(),
                    "avg_logprob": round(s.avg_logprob, 4),
                    "no_speech_prob": round(s.no_speech_prob, 4),
                    "words": [{"start": round(w.start, 3), "end": round(w.end, 3),
                               "word": w.word, "probability": round(w.probability, 4)}
                              for w in (s.words or [])],
                })
            part = {"language": info.language,
                    "language_probability": round(info.language_probability, 4),
                    "segments": part_segs,
                    "infer_sec": round(time.time() - t0, 3)}
            with open(cjson, "w", encoding="utf-8") as f:
                json.dump(part, f, ensure_ascii=False)
            log(f"  [块{ci + 1}/{n_chunks}] {len(part_segs)} 句  "
                f"{part['infer_sec']:.1f}s  ({cd / max(part['infer_sec'], 1e-6):.1f}x)")

        langs.append(part.get("language") or "?")
        infer_total += part.get("infer_sec", 0.0)
        # 时间轴平移到全片
        for s in part["segments"]:
            s2 = dict(s)
            s2["start"] = round(s["start"] + cs, 3)
            s2["end"] = round(s["end"] + cs, 3)
            s2["words"] = [{"start": round(w["start"] + cs, 3),
                            "end": round(w["end"] + cs, 3),
                            "word": w["word"], "probability": w["probability"]}
                           for w in s.get("words", [])]
            total_words += len(s2["words"])
            all_segs.append(s2)

    all_segs.sort(key=lambda x: x["start"])
    for i, s in enumerate(all_segs, 1):
        s["id"] = i

    lang = max(set(langs), key=langs.count) if langs else "?"
    result = {
        "meta": {
            "source_file": src,
            "model": a.model, "device": a.device, "compute_type": ct,
            "language": lang, "duration_sec": round(total, 3),
            "segment_count": len(all_segs), "word_count": total_words,
            "chunk_min": a.chunk_min, "chunk_count": n_chunks,
            "inference_sec": round(infer_total, 3),
            "speedup_vs_realtime": round(total / infer_total, 1) if infer_total else 0,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "backend": "faster-whisper (CTranslate2) + chunked",
        },
        "segments": all_segs,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    md = result["meta"]
    log(f"\n[done] {md['segment_count']} 句 / {total_words} 词 / "
        f"语言 {lang} / 推理 {infer_total:.0f}s ({md['speedup_vs_realtime']}x)")
    log(f"[out] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
