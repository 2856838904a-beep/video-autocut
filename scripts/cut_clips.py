r"""
cut_clips.py —— 按切点导出片段(视频 + 指定解说音轨)

为什么不用"让剪映直接读原 MKV":
  原 MKV 有 4 条音轨,剪映默认只会读第一条(游戏声),解说会丢。
  所以先由脚本把「画面 + 解说轨」合成成独立 mp4,再交给剪映 —— 结果确定。

用 N 卡硬编(h264_nvenc),速度大约是软编的 5~10 倍。

用法:
  python cut_clips.py --cuts cuts.json --src "G:\...\x.mkv" --track 3
                      --dst outdir [--audio-track-index N] [--crf 20]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

FFMPEG = paths.ffmpeg()


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 60:02d}{t % 60:02d}"


def have_nvenc() -> bool:
    r = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                       capture_output=True, text=True, errors="replace")
    return "h264_nvenc" in (r.stdout or "")


def _auto_voice_gain(voice_wav: str) -> float:
    """算解说轨需要提升多少 dB(目标中位 -14 dBFS,夹在 0~40)。"""
    try:
        HERE = os.path.dirname(os.path.abspath(__file__))
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        from mix_audio import median_level_db
        lv = median_level_db(voice_wav)
        return max(0.0, min(40.0, -14.0 - lv))
    except Exception:
        return 20.0


def cut_one(src: str, s0: float, s1: float, a_index: int, dst: str,
            use_nvenc: bool, crf: int,
            voice_wav: str | None = None, voice_gain: float | None = None,
            game_gain: float = -3.0, denoise: bool = True,
            voice_out: str | None = None) -> dict:
    """
    导出单个片段。a_index 是**音频流序号**(a:N 的 N)。

    两种模式:
      voice_out 给定 -> **分离模式**:视频片段只含游戏原声,解说出成独立音频文件
                        (用于"解说单独一条轨",剪映里可分别调音量)
      voice_wav 给定(且无 voice_out) -> **混音模式**:两条混成一个音频流

    ⚠️ 早先只 -map 一条轨,导致 MC 那批丢了游戏原声、RDR 那批丢了玩家解说。
    """
    dur = s1 - s0
    venc = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf), "-b:v", "0"]
            if use_nvenc else
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf)])

    # --- 分离模式:视频只带游戏原声;解说另外导一个文件 ---
    if voice_wav and voice_out:
        g = _auto_voice_gain(voice_wav) if voice_gain is None else voice_gain
        vchain = (("afftdn=nf=-25:nr=12," if denoise else "")
                  + f"volume={g:.1f}dB,"
                  + "acompressor=threshold=-18dB:ratio=3:attack=8:release=250:makeup=1,"
                  + "highpass=f=90")
        cmd_v = ([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                  "-ss", f"{s0:.3f}", "-t", f"{dur:.3f}", "-i", src,
                  "-map", "0:v:0", "-map", f"0:a:{a_index}"]
                 + venc + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                           "-ac", "2", "-movflags", "+faststart", dst])
        # 解说:同一时间窗,提升 + 降噪 + 压缩,导出为 m4a
        cmd_a = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-ss", f"{s0:.3f}", "-t", f"{dur:.3f}", "-i", voice_wav,
                 "-af", vchain + ",alimiter=limit=0.89:level=disabled",
                 "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                 voice_out]
        t0 = time.time()
        r1 = subprocess.run(cmd_v, capture_output=True, text=True, errors="replace")
        r2 = subprocess.run(cmd_a, capture_output=True, text=True, errors="replace")
        ok = (r1.returncode == 0 and r2.returncode == 0
              and os.path.exists(dst) and os.path.getsize(dst) > 10000
              and os.path.exists(voice_out) and os.path.getsize(voice_out) > 2000)
        return {"ok": ok, "sec": round(time.time() - t0, 2),
                "mb": round(os.path.getsize(dst) / 1e6, 2) if os.path.exists(dst) else 0,
                "mode": "separate", "voice_gain": round(g, 1),
                "voice_file": voice_out if os.path.exists(voice_out) else None,
                "err": ((r1.stderr or "") + (r2.stderr or ""))[-400:] if not ok else ""}

    if voice_wav and os.path.exists(voice_wav):
        if voice_gain is None:
            voice_gain = _auto_voice_gain(voice_wav)
        vchain = (("afftdn=nf=-25:nr=12," if denoise else "")
                  + f"volume={voice_gain:.1f}dB,"
                  + "acompressor=threshold=-18dB:ratio=3:attack=8:release=250:makeup=1,"
                  + "highpass=f=90")
        gchain = "" if game_gain == 0 else f"volume={game_gain:.1f}dB,"
        fc = (f"[0:a:{a_index}]{gchain}asetpts=N/SR/TB[g];"
              f"[1:a]{vchain},asetpts=N/SR/TB[v];"
              f"[g][v]amix=inputs=2:duration=longest:normalize=0,"
              f"alimiter=limit=0.89:level=disabled[out]")
        cmd = ([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{s0:.3f}", "-t", f"{dur:.3f}", "-i", src,
                "-ss", f"{s0:.3f}", "-t", f"{dur:.3f}", "-i", voice_wav,
                "-filter_complex", fc, "-map", "0:v:0", "-map", "[out]"]
               + venc + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                         "-ac", "2", "-movflags", "+faststart", dst])
    else:
        cmd = ([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{s0:.3f}", "-t", f"{dur:.3f}", "-i", src,
                "-map", "0:v:0", "-map", f"0:a:{a_index}"]
               + venc + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                         "-ac", "2", "-movflags", "+faststart", dst])

    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    ok = r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 10000
    return {"ok": ok, "sec": round(time.time() - t0, 2),
            "mb": round(os.path.getsize(dst) / 1e6, 2) if os.path.exists(dst) else 0,
            "mixed": bool(voice_wav),
            "voice_gain": None if voice_gain is None else round(voice_gain, 1),
            "err": (r.stderr or "")[-400:] if not ok else ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuts", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--track", type=int, default=3,
                    help="【游戏原声】所在的绝对音轨号(stream 索引,不是 a:N)")
    ap.add_argument("--a-index", type=int, default=None,
                    help="游戏原声在音频流里的序号(a:N 的 N)。默认由 --track 换算")
    ap.add_argument("--voice-wav", default=None,
                    help="【解说】的独立 wav。给了就把游戏原声与解说混成一条音轨 —— "
                         "两条都保留。不给则片段里只有游戏原声")
    ap.add_argument("--voice-gain", type=float, default=None,
                    help="解说提升量(dB)。默认按实测电平自动算(目标中位 -14 dBFS)")
    ap.add_argument("--game-gain", type=float, default=-3.0,
                    help="游戏原声增益(dB),默认 -3 给解说让位")
    ap.add_argument("--no-denoise", action="store_true", help="不对解说做降噪")
    ap.add_argument("--voice-separate", action="store_true",
                    help="【分离模式】视频片段只带游戏原声,解说导出为同名 .voice.m4a "
                         "独立文件 —— 用于在剪映里把解说放单独一条音频轨,可分别调音量")
    ap.add_argument("--dst", required=True)
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0, help="只导前 N 个(调试用)")
    a = ap.parse_args()

    cuts = json.load(open(a.cuts, encoding="utf-8"))["cuts"]
    if a.limit:
        cuts = cuts[: a.limit]
    os.makedirs(a.dst, exist_ok=True)

    # 绝对 stream 索引 -> 音频流序号
    if a.a_index is None:
        r = subprocess.run([FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"),
                            "-v", "error", "-select_streams", "a",
                            "-show_entries", "stream=index", "-of", "json", a.src],
                           capture_output=True, text=True, errors="replace")
        idxs = [s["index"] for s in paths.parse_probe_json(r.stdout)["streams"]]
        if a.track not in idxs:
            print(f"音轨 {a.track} 不在 {idxs} 里", file=sys.stderr)
            return 2
        a.a_index = idxs.index(a.track)

    use_nvenc = have_nvenc()
    mix = bool(a.voice_wav and os.path.exists(a.voice_wav))
    print(f"源          {os.path.basename(a.src)}")
    print(f"游戏原声轨  绝对索引 {a.track}  ->  a:{a.a_index}")
    if mix:
        g = a.voice_gain if a.voice_gain is not None else _auto_voice_gain(a.voice_wav)
        print(f"解说轨      {os.path.basename(a.voice_wav)}  提升 {g:.1f} dB  "
              f"游戏 {a.game_gain:+.1f} dB  {'(降噪)' if not a.no_denoise else ''}")
    else:
        print(f"解说轨      无 —— 片段只含游戏原声")
    print(f"编码        {'h264_nvenc(硬编)' if use_nvenc else 'libx264(软编)'}  crf={a.crf}")
    print(f"片段        {len(cuts)} 个\n")

    base = os.path.splitext(os.path.basename(a.src))[0]
    results = []
    total_in = 0.0
    t_all = time.time()
    for i, c in enumerate(cuts, 1):
        name = f"{base}_cut{i:02d}_{fmt(c['start'])}-{fmt(c['end'])}.mp4"
        dst = os.path.join(a.dst, name)
        r = cut_one(a.src, c["start"], c["end"], a.a_index, dst, use_nvenc, a.crf,
                    voice_wav=a.voice_wav if mix else None,
                    voice_gain=a.voice_gain, game_gain=a.game_gain,
                    denoise=not a.no_denoise,
                    voice_out=(os.path.splitext(dst)[0] + ".voice.m4a")
                    if (mix and a.voice_separate) else None)
        total_in += c["dur"]
        r.update({"file": dst, "name": name, "start": c["start"], "end": c["end"],
                  "dur": c["dur"], "text": c.get("text", "")})
        results.append(r)
        tag = "OK  " if r["ok"] else "FAIL"
        print(f"  #{i:>2} {tag} {name:<46} {r['mb']:>6.2f} MB  {r['sec']:>5.2f}s")
        if not r["ok"]:
            print(f"        {r['err'][:240]}")

    el = time.time() - t_all
    okn = sum(1 for r in results if r["ok"])
    print(f"\n完成 {okn}/{len(results)}  素材总时长 {total_in:.1f}s  耗时 {el:.1f}s "
          f"({total_in / max(el, 1e-6):.1f}x 实时)")

    man = os.path.join(a.dst, "_clips.json")
    with open(man, "w", encoding="utf-8") as f:
        json.dump({"src": a.src, "game_track": a.track, "a_index": a.a_index,
                   "voice_wav": a.voice_wav if mix else None,
                   "mixed": mix,
                   "clips": [r for r in results if r["ok"]]}, f,
                  ensure_ascii=False, indent=1)
    print(f"-> {man}")
    return 0 if okn == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
