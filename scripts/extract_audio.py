r"""
extract_audio.py —— 批量抽音轨(自动挑选解说轨)

★ 为什么需要"自动挑选":OBS 分轨录制会在一个 MKV 里放 4 条独立音轨
  (simple_aac_recording0..3),分别是游戏声/浏览器/麦克风等。
  默认的 -map 0:a:0 只取第一条,常常是游戏声 —— 会把人声整个漏掉,
  误判成"这段没解说"。首版脚本就踩了这个坑。

做法:先对每条音轨采样测活跃度(>-35dB 的时间占比),挑最活跃的那条抽出。

用法:
  python extract_audio.py --src "G:\录像\MC" --dst "_audio" [--jobs 2] [--track auto|N]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

import paths

FFMPEG = paths.ffmpeg()
FFPROBE = paths.ffprobe()
VIDEO_EXT = (".mkv", ".mp4", ".flv", ".mov", ".avi", ".ts", ".m4v", ".wmv")


def probe_duration(p: str) -> float:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", p], capture_output=True, text=True, errors="replace")
    try:
        return float(paths.parse_probe_json(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def audio_indices(path: str) -> list[int]:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "json", path],
                       capture_output=True, text=True, errors="replace")
    try:
        return [s["index"] for s in paths.parse_probe_json(r.stdout)["streams"]]
    except Exception:
        return []


def track_activity(path: str, aidx: int, dur: float) -> float:
    """
    采样多段,返回该音轨 >-35dB 的**最大**时间占比。

    ⚠️ 两个实测教训(别改回去):
      1. 采样点不能少。首版只采 4 个点,把「开头静默、中段有解说」的文件
         误判成"几乎无解说" —— 复核发现实际有采样段高达 53.8%。
      2. 判据用 **max 而不是 mean**。解说活跃度随时间剧烈波动(开场热、
         中段挂机、后面又热),取平均会把有内容的轨判低。只要任一采样段
         明显活跃,这条轨就承载过解说。
    """
    picks = [0.03, 0.12, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
    act = []
    for f in picks:
        st = max(0.0, dur * f)
        cmd = [FFMPEG, "-hide_banner", "-nostats", "-ss", f"{st:.1f}", "-t", "45",
               "-i", path, "-map", f"0:{aidx}", "-vn", "-ac", "1", "-ar", "8000",
               "-f", "f32le", "-"]
        r = subprocess.run(cmd, capture_output=True)
        x = np.frombuffer(r.stdout, dtype=np.float32)
        if x.size < 8000:
            continue
        step = 2000
        n = x.size // step
        y = x[: n * step].reshape(n, step)
        ms = np.einsum("ij,ij->i", y, y, dtype=np.float64) / step
        db = 20 * np.log10(np.maximum(np.sqrt(ms), 1e-9))
        act.append(float((db > -35).mean()))
    if not act:
        return 0.0
    return float(max(act))


def pick_track(src: str, dur: float, mode: str) -> tuple[int | None, dict]:
    """
    挑解说轨。

    ★ 判据是 ASR 转写结果,不是能量。实测反例:
        2026-07-05_10-17-40.mkv  轨1 能量 100% → 转写中文字 0(纯游戏音效)
                                 轨3 能量  54% → 中文 49.6 字/分钟(真解说)
        2026-07-04_13-45-54.mkv  轨1(能量最高)→ 0 字/分;轨2 → 73.5 字/分
      能量只能说明"有声音",不能说明"有解说"。游戏音效常常比人声更响。
      ⚠️ 也不要用"取最大值"来加强能量判据 —— 那会放大这个错误。

    流程:先用能量粗筛出候选(顺带确定顺序),再对候选做快速转写,
          按「中文字/分钟」定胜负。转写质量才是唯一可靠的鉴别器。
    """
    idxs = audio_indices(src)
    if not idxs:
        return None, {}
    if mode != "auto":
        want = int(mode)
        return (want if want in idxs else idxs[0]), {}

    # --- 能量粗筛 ---
    energy = {}
    for ai in idxs:
        energy[ai] = round(track_activity(src, ai, dur), 3)

    # 候选:按能量排序的前 2 条 + 任何能量 >25% 的轨(避免漏掉真正的解说轨)
    ranked = sorted(idxs, key=lambda k: -energy[k])
    cands = list(dict.fromkeys(
        ranked[:2] + [k for k in idxs if energy[k] > 0.25]))
    if not cands:
        cands = ranked[:1]

    # --- ASR 验证:对全部音轨都测 ---
    # ⚠️ 不要用能量做候选过滤!实测反例:某文件最优的轨2 能量采样为 0%,
    #    被"候选"过滤掉后就永远选不到它(轨4 能量 80% 但只有 22.5 字/分,
    #    轨2 能量 0% 却有 73.5 字/分)。能量与解说完全解耦,必须逐轨实测。
    scores = asr_scores(src, idxs, dur, seconds=30.0)
    if scores:
        stats = {}
        for k in idxs:
            st = {"energy": energy[k]}
            if k in scores:
                st["cpm"] = scores[k].get("cpm", 0.0)
                st["text"] = scores[k].get("text", "")
            stats[k] = st
        best = max(scores, key=lambda k: scores[k].get("cpm", 0.0))
        return best, stats
    # 转写不可用时退回能量
    best = max(energy, key=lambda k: energy[k])
    return best, {k: {"energy": v} for k, v in energy.items()}


def asr_scores(src: str, cands: list[int], dur: float,
               seconds: float = 40.0) -> dict[int, dict]:
    """对候选轨做快速转写,返回 {轨号: {cpm: 中文字/分钟, text: 前几字}}。"""
    try:
        import re
        import tempfile
        from faster_whisper import WhisperModel
    except ImportError:
        return {}

    cjk = re.compile(r"[\u4e00-\u9fff]")
    dev = "cpu"
    try:
        import torch
        dev = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        pass
    ct = "float16" if dev == "cuda" else "int8"
    try:
        model = WhisperModel("small", device=dev, compute_type=ct)
    except Exception:
        return {}

    tmp = tempfile.mkdtemp(prefix="picktrk_")
    out = {}
    for ai in cands:
        parts = []
        for k, f in enumerate([0.2, 0.55, 0.85]):
            pp = os.path.join(tmp, f"s{ai}_{k}.wav")
            subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                            "-ss", f"{max(0.0, dur * f):.1f}", "-t", f"{seconds:.0f}",
                            "-i", src, "-map", f"0:{ai}", "-vn", "-ac", "1", "-ar", "16000",
                            "-c:a", "pcm_s16le", pp], capture_output=True)
            if os.path.exists(pp) and os.path.getsize(pp) > 8000:
                parts.append(pp)
        if not parts:
            out[ai] = {"cpm": 0.0, "text": ""}
            continue
        lst = os.path.join(tmp, f"l{ai}.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            for pp in parts:
                fh.write(f"file '{pp.replace(chr(92), '/')}'\n")
        wav = os.path.join(tmp, f"c{ai}.wav")
        subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", wav],
                       capture_output=True)
        for pp in parts:
            try:
                os.remove(pp)
            except OSError:
                pass
        if not os.path.exists(wav):
            out[ai] = {"cpm": 0.0, "text": ""}
            continue
        try:
            segs, _ = model.transcribe(wav, language=None, beam_size=5,
                                       vad_filter=True, condition_on_previous_text=False)
            segs = list(segs)
        except Exception:
            segs = []

        # ★ 幻觉过滤:近乎静音的轨会被 Whisper "编"出通顺文本
        #   实测反例:某轨能量 -68dB(几乎无声),却"转出" 253 字/分,
        #   特征是 no_speech_prob ≈ 0.92。不过滤就会把静音轨误判成解说轨。
        real = [s for s in segs if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
        text = "".join(s.text for s in real)
        if not real:
            # 全部判为幻觉 —— 视为无解说
            out[ai] = {"cpm": 0.0, "text": "", "halluc": len(segs),
                       "dropped": len(segs)}
        else:
            sec = len(parts) * seconds
            out[ai] = {
                "cpm": round(len(cjk.findall(text)) / max(sec / 60.0, 1e-6), 1),
                "text": text[:60],
                "dropped": len(segs) - len(real),
                "seg_total": len(segs),
            }
        try:
            os.remove(wav)
        except OSError:
            pass
    return out


def extract_one(src: str, dst_dir: str, mode: str) -> dict:
    base = os.path.splitext(os.path.basename(src))[0]
    dst = os.path.join(dst_dir, f"{base}.wav")
    side = os.path.join(dst_dir, f"{base}.track.json")

    if os.path.exists(dst) and os.path.exists(side) and os.path.getsize(dst) > 10000:
        info = json.load(open(side, encoding="utf-8"))
        return {"src": src, "dst": dst, "skipped": True, "track": info.get("track"),
                "stats": info.get("stats"), "mb": round(os.path.getsize(dst) / 1e6, 1)}

    t0 = time.time()
    dur = probe_duration(src)
    ai, stats = pick_track(src, dur, mode)
    if ai is None:
        return {"src": src, "ok": False, "err": "无音轨"}

    r = subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
         "-map", f"0:{ai}", "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", dst],
        capture_output=True, text=True, errors="replace",
    )
    ok = r.returncode == 0 and os.path.exists(dst)
    if ok:
        with open(side, "w", encoding="utf-8") as f:
            json.dump({"track": ai, "stats": stats, "duration": dur}, f)
    return {
        "src": src, "dst": dst, "ok": ok, "skipped": False, "track": ai,
        "stats": stats, "sec": round(time.time() - t0, 1),
        "mb": round(os.path.getsize(dst) / 1e6, 1) if ok else 0,
        "err": (r.stderr or "")[-200:] if not ok else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--track", default="auto",
                    help="auto = 按活跃度自动挑解说轨;也可以给具体索引如 3")
    a = ap.parse_args()

    os.makedirs(a.dst, exist_ok=True)
    files = []
    for root, _, names in os.walk(a.src):
        for nm in names:
            if nm.lower().endswith(VIDEO_EXT):
                files.append(os.path.join(root, nm))
    files.sort()
    if not files:
        print("没找到素材", file=sys.stderr)
        return 2

    print(f"待处理 {len(files)} 个素材,并发 {a.jobs},轨道模式 {a.track}")
    t0 = time.time()
    total_mb = 0.0
    done = 0
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(extract_one, f, a.dst, a.track): f for f in files}
        for fu in as_completed(futs):
            r = fu.result()
            done += 1
            total_mb += r.get("mb", 0)
            name = os.path.basename(r["src"])
            if r.get("skipped"):
                print(f"  [{done:>2}/{len(files)}] skip {name:<30} "
                      f"轨道{r.get('track')}  {r.get('mb', 0):>7.1f} MB")
            elif r.get("ok"):
                st = r.get("stats") or {}
                picked = r.get("track")

                # stats 形状现在有两种:
                #   固定轨模式       -> {轨号: 活跃度float}
                #   auto + ASR 模式  -> {轨号: {"energy":…, "cpm":…, "text":…}}
                def fmt_track(k, v):
                    if isinstance(v, dict):
                        e = v.get("energy")
                        c = v.get("cpm")
                        parts = []
                        if e is not None:
                            parts.append(f"能量{e * 100:.0f}%")
                        if c is not None:
                            parts.append(f"{c:.0f}字/分")
                        mark = "*" if k == picked else ""
                        return f"a{k}{mark}({','.join(parts)})"
                    return f"a{k}={v * 100:.0f}%"

                stxt = " ".join(fmt_track(k, v)
                                for k, v in sorted(st.items(), key=lambda x: int(x[0])))
                print(f"  [{done:>2}/{len(files)}] OK   {name:<30} "
                      f"轨道{r['track']:<2} {r.get('mb', 0):>7.1f} MB  {r.get('sec')}s")
                if stxt:
                    print(f"        {stxt}")
            else:
                print(f"  [{done:>2}/{len(files)}] FAIL {name:<30} {r.get('err', '')[:90]}")

    print(f"\n完成 {done} 个,共 {total_mb:.1f} MB,耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
