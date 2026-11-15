r"""
analyze_tracks.py —— 多音轨素材的音轨结构分析(标准第一步)

OBS 分轨录制的 MKV 常有多条音轨,但**不是每条都必要**:
  - 可能有**重复副本**(实测大镖客:轨1 与轨4 的转写文本几乎逐字相同)
  - 可能有**空轨**(轨2 只有 -85 dB 底噪)
  - 可能是**同一路信号的副本**(单声道复制成立体声,左右完全相同)
  - 真正的有效内容往往只有两类:**游戏原声** 与 **游玩解说**

本脚本按顺序做四件事:
  ① 列出全部音轨及其基本属性(声道数、电平)
  ② 检测重复:两两比对 —— 波形相关度 + 转写文本重合度
  ③ 判定每条轨的内容类型(游戏原声 / 候选解说 / 静音 / 未接线)
  ④ 给出"最终该用哪两条"的建议

为什么要判重复:重复轨会让后续选段重复计算、也会让"解说轨"挑错 ——
实测把游戏原声的副本当成了候选轨。

用法:
  python analyze_tracks.py --src <视频> [--out tracks.json]
  python analyze_tracks.py --src <目录> --batch      # 批量处理目录下所有文件
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

import paths

FFMPEG = paths.ffmpeg()
FFPROBE = paths.ffprobe()
CJK = re.compile(r"[\u4e00-\u9fff]")

# 判定阈值
DUP_CORR = 0.97          # 波形相关系数 >= 此值判为同一信号
DUP_TEXT = 0.80          # 转写文本重合度 >= 此值判为同一内容
SILENT_DBFS = -75.0      # 中位电平低于此值视为空轨/未接线
SPEECH_GOOD = -40.0      # 语音电平门槛(GB 参考 classify_voice.py)
SNR_GOOD = 25.0


def probe_streams(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_format", "-show_streams",
                        "-of", "json", path],
                       capture_output=True, text=True, errors="replace")
    try:
        j = paths.parse_probe_json(r.stdout)
    except Exception:
        return None, []
    dur = float(j.get("format", {}).get("duration") or 0)
    return dur, [s for s in j["streams"] if s["codec_type"] == "audio"]


def audio_windows(path, aidx, dur, n_pick=8, sec=20.0, sr=8000):
    """取若干段单声道 PCM,返回 (波形片段列表, 每窗 dB)。"""
    chunks, dbs = [], []
    for k in range(n_pick):
        f = (k + 0.5) / n_pick
        st = max(0.0, dur * f)
        cmd = [FFMPEG, "-hide_banner", "-nostats", "-ss", f"{st:.1f}",
               "-t", f"{sec:.1f}", "-i", path, "-map", f"0:{aidx}",
               "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
        r = subprocess.run(cmd, capture_output=True)
        x = np.frombuffer(r.stdout, dtype=np.float32)
        if x.size < sr:
            continue
        chunks.append(x)
        st2 = sr
        n = x.size // st2
        y = x[: n * st2].reshape(n, st2)
        ms = np.einsum("ij,ij->i", y, y, dtype=np.float64) / st2
        dbs.append(20 * np.log10(np.maximum(np.sqrt(ms), 1e-9)))
    return chunks, (np.concatenate(dbs) if dbs else np.array([]))


def waveform_corr(a_chunks, b_chunks):
    """
    两条轨在同时间点的波形相关系数。取各段的最小值(保守)。

    ⚠️ 必须排除"静音伪相关":两条几乎无声的轨之间,噪声也能算出 0.99 的相关度
    (实测把静音轨判成了"同一信号副本")。所以要求两段都有实际信号才计入。
    """
    MIN_RMS = 0.002          # 低于此视为无信号(-54 dBFS 量级)
    cors = []
    for a, b in zip(a_chunks, b_chunks):
        n = min(a.size, b.size)
        if n < 8000:
            continue
        a2, b2 = a[:n], b[:n]
        ra = float(np.sqrt((a2.astype(np.float64) ** 2).mean()))
        rb = float(np.sqrt((b2.astype(np.float64) ** 2).mean()))
        # 任一段基本无声 -> 不可比,跳过该段
        if ra < MIN_RMS or rb < MIN_RMS:
            continue
        if a2.std() < 1e-6 or b2.std() < 1e-6:
            continue
        # 电平差过大说明不是同一信号(副本的电平应几乎相同)
        if abs(20 * np.log10(ra / rb)) > 3.0:
            cors.append(0.0)
            continue
        cors.append(float(np.corrcoef(a2, b2)[0, 1]))
    return min(cors) if cors else None


def transcribe_short(path, aidx, dur, model, n=3, sec=40.0):
    """短转写,用于文本层面的重复判定与语种判定。"""
    parts, td = [], tempfile.mkdtemp(prefix="trk_")
    for k in range(n):
        f = (k + 0.5) / n
        w = os.path.join(td, f"s{k}.wav")
        subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{max(0.0, dur * f):.1f}", "-t", f"{sec:.0f}",
                        "-i", path, "-map", f"0:{aidx}", "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", w], capture_output=True)
        if os.path.exists(w) and os.path.getsize(w) > 8000:
            parts.append(w)
    if not parts:
        return "", "?", 0.0
    lst = os.path.join(td, "l.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")
    cat = os.path.join(td, "c.wav")
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", cat],
                   capture_output=True)
    text, lang = "", "?"
    if os.path.exists(cat):
        try:
            segs, info = model.transcribe(cat, language=None, beam_size=5,
                                          vad_filter=True,
                                          condition_on_previous_text=False)
            segs = [s for s in segs if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
            text = "".join(s.text for s in segs)
            lang = info.language
        except Exception:
            pass
    return text, lang, len(parts) * sec


def text_overlap(a: str, b: str) -> float:
    """
    两段文本的重合度。取两种度量的较大值:
      字符二元组 Jaccard —— 对中文有效
      词级 Jaccard        —— 对英文/混合文本有效(4-gram 对英文短句太严格,会漏判)
    实测教训:只用 4-gram 时,英文游戏对白的两条重复轨只拿到 0.2~0.3,判不出重复。
    """
    def norm(t):
        return re.sub(r"\s+", "", t or "")
    ta, tb = norm(a), norm(b)
    if len(ta) < 8 or len(tb) < 8:
        return 0.0

    def jac(ga, gb):
        if not ga or not gb:
            return 0.0
        return len(ga & gb) / len(ga | gb)

    # 字符二元组
    ca = {ta[i:i + 2] for i in range(len(ta) - 1)}
    cb = {tb[i:i + 2] for i in range(len(tb) - 1)}
    char_j = jac(ca, cb)
    # 词级
    wa = set(re.findall(r"[a-zA-Z']+|[\u4e00-\u9fff]", (a or "").lower()))
    wb = set(re.findall(r"[a-zA-Z']+|[\u4e00-\u9fff]", (b or "").lower()))
    word_j = jac(wa, wb)
    return max(char_j, word_j)


def analyze_file(path, model, verbose=True):
    dur, streams = probe_streams(path)
    if not streams:
        return None
    if verbose:
        print(f"\n{'=' * 90}")
        print(f"{os.path.basename(path)}   时长 {dur / 60:.1f} 分钟   音轨 {len(streams)} 条")
        print(f"{'=' * 90}")

    info = []
    for s in streams:
        ai = s["index"]
        chunks, dbs = audio_windows(path, ai, dur)
        med = float(np.median(dbs)) if dbs.size else -180.0
        speech = float(np.percentile(dbs, 90)) if dbs.size else -180.0
        noise = float(np.percentile(dbs, 10)) if dbs.size else -180.0
        text, lang, _ = transcribe_short(path, ai, dur, model) if med > SILENT_DBFS else ("", "?", 0)
        info.append({
            "track": ai, "channels": s.get("channels"),
            "median_dbfs": round(med, 1), "speech_dbfs": round(speech, 1),
            "noise_dbfs": round(noise, 1),
            "snr_db": round(speech - noise, 1),
            "lang": lang, "cjk": len(CJK.findall(text)),
            "text": text[:80], "_chunks": chunks, "_text": text,
        })

    # ② 重复/同内容检测 —— 分清两件事:
    #    A) 同一信号的副本:波形相关度 >= 0.97 → 真正重复,直接废弃一条
    #    B) 同内容的不同混音:波形不相关但含相同对白 → 不是重复,但内容等价,
    #       选一条用即可(实测大镖客:轨1 与轨4 相关度 -0.023,却含完全相同的英文对白,
    #       RMS 也几乎相同 —— 是同一内容的两个独立混音,不是副本)
    #    这两者的处理不同:副本是"多余",同内容是"等价但可选"。
    for i in range(len(info)):
        for j in range(i + 1, len(info)):
            a, b = info[i], info[j]
            corr = waveform_corr(a["_chunks"], b["_chunks"])
            ov = text_overlap(a["_text"], b["_text"])
            if corr is not None and corr >= DUP_CORR:
                b.setdefault("_is_dup_of", []).append(a["track"])
                a.setdefault("_has_dup", []).append(b["track"])
                a.setdefault("_dup_notes", []).append(
                    f"轨{a['track']} = 轨{b['track']}  【同一信号副本】波形相关 {corr:.3f} → 废弃轨{b['track']}")
            elif ov >= DUP_TEXT:
                b.setdefault("_same_content_as", []).append(a["track"])
                a.setdefault("_same_content", []).append(b["track"])
                a.setdefault("_dup_notes", []).append(
                    f"轨{a['track']} ≈ 轨{b['track']}  【同内容不同混音】文本重合 {ov:.2f}、"
                    f"波形不相关({corr if corr is None else round(corr, 3)}) → 内容等价,选一条即可")

    # ③ 内容类型判定
    #
    # ⚠️ 这里的判据要分清两条独立的线,不要混用:
    #   游戏原声:看【语种 + 语音能量】。不要用信噪比 —— 动作游戏的原声连续不断、
    #             没有静默间隙,信噪比天然就低(实测 01-31 是 11.4 dB),
    #             用信噪比会把游戏原声误判成"持续背景声"。
    #   解说轨:  看【中文文本量 + 语音电平 + 信噪比】。解说有自然停顿,
    #             所以信噪比是有效判据;信噪比低说明语音被底噪淹没。
    for x in info:
        is_en = (x["lang"] == "en")
        has_zh_text = x["cjk"] >= 8
        if x["median_dbfs"] < SILENT_DBFS:
            x["kind"] = "静音/未接线"
        elif x.get("_is_dup_of"):
            x["kind"] = f"重复副本(同轨{x['_is_dup_of'][0]})"
        elif x.get("_same_content_as"):
            prev = next((y for y in info if y["track"] == x["_same_content_as"][0]), None)
            base = prev.get("kind", "未知") if prev else "未知"
            x["kind"] = f"等价于轨{x['_same_content_as'][0]}({base})"
        elif has_zh_text and x["speech_dbfs"] >= SPEECH_GOOD and x["snr_db"] >= SNR_GOOD:
            x["kind"] = "游戏解说"
        elif has_zh_text and x["speech_dbfs"] >= SPEECH_GOOD:
            x["kind"] = "解说(底噪偏大)"
        elif has_zh_text:
            x["kind"] = "解说(电平偏低)"
        elif is_en and x["speech_dbfs"] > -45:
            # 英语 + 有语音能量 = 游戏原声(含对白/音效/配乐)
            x["kind"] = "游戏原声"
        elif x["speech_dbfs"] - x["noise_dbfs"] < 12:
            x["kind"] = "持续背景声"
        else:
            x["kind"] = "游戏原声"

    if verbose:
        print(f"\n{'轨':>3} {'声道':>4} {'中位dB':>8} {'语音dB':>8} {'底噪dB':>8} "
              f"{'信噪比':>7} {'语种':>4} {'中文':>5}  类型")
        print("-" * 90)
        for x in info:
            print(f"{x['track']:>3} {str(x['channels']):>4} {x['median_dbfs']:>8.1f} "
                  f"{x['speech_dbfs']:>8.1f} {x['noise_dbfs']:>8.1f} {x['snr_db']:>7.1f} "
                  f"{x['lang']:>4} {x['cjk']:>5}  {x['kind']}")
        print("\n重复关系:")
        shown = False
        for x in info:
            for n in x.get("_dup_notes", []):
                print(f"  {n}")
                shown = True
        if not shown:
            print("  (无重复轨)")

    # ④ 建议:游戏原声优先取"非重复"的那条;若都是副本,取音轨号最小的
    game = None
    for x in info:
        if x["kind"] == "游戏原声":
            if game is None or x["speech_dbfs"] > game["speech_dbfs"]:
                game = x
    voice = None
    for x in info:
        if x["kind"] in ("游戏解说", "解说(底噪偏大)", "解说(电平偏低)"):
            if voice is None or x["cjk"] > voice["cjk"]:
                voice = x
    if verbose:
        dup_note = ""
        if game and game.get("_has_dup"):
            dup_note = f"(轨{game['track']} 与轨{game['_has_dup'][0]} 内容重复,废弃后者)"
        print(f"\n建议: 游戏原声 = 轨{game['track'] if game else '无'} {dup_note}"
              f"   解说 = 轨{voice['track'] if voice else '无'}")
        if voice and voice["kind"] == "解说(电平偏低)":
            print(f"      ⚠️ 解说电平偏低({voice['speech_dbfs']:.0f} dBFS,"
                  f"信噪比 {voice['snr_db']:.0f} dB)—— 建议单独成轨而非混入主音轨")

    for x in info:
        x.pop("_chunks", None)
        x.pop("_text", None)
        x.pop("_is_dup_of", None)
        x.pop("_has_dup", None)
    return {"file": os.path.basename(path), "duration": dur,
            "tracks": info,
            "dup_notes": [n for x in info for n in x.get("_dup_notes", [])],
            "game_track": game["track"] if game else None,
            "voice_track": voice["track"] if voice else None,
            "voice_ok": bool(voice and voice["kind"] == "游戏解说")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from faster_whisper import WhisperModel
    import torch
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = WhisperModel("small", device=dev, compute_type="float16" if dev == "cuda" else "int8")

    if not args.batch:
        res = analyze_file(os.path.abspath(args.src), model)
        op = args.out or os.path.splitext(args.src)[0] + ".tracks_analysis.json"
        if res:
            with open(op, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=1)
            print(f"\n-> {op}")
        return 0

    files = sorted(f for f in os.listdir(args.src)
                   if f.lower().endswith((".mkv", ".mp4")) and
                   os.path.getsize(os.path.join(args.src, f)) > 1e6)
    print(f"批量分析 {len(files)} 个文件\n")
    results = []
    for i, f in enumerate(files, 1):
        print(f"\n### [{i}/{len(files)}] {f}")
        r = analyze_file(os.path.join(args.src, f), model, verbose=False)
        if r:
            results.append(r)
            tr = " ".join(f"{t['track']}:{t['kind']}" for t in r["tracks"])
            print(f"    {tr}")
            print(f"    → 游戏轨 {r['game_track']}  解说轨 {r['voice_track']}"
                  f"{'' if r['voice_ok'] else '  (解说质量不足,建议单独成轨)'}")
    op = args.out or os.path.join(args.src, "_tracks_analysis.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
