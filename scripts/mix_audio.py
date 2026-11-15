r"""
mix_audio.py —— 把「游戏原声」和「解说」两条轨混成一条(两条都要保留)

背景:两批素材都是 OBS 分轨录制,游戏原声与解说在**不同的音轨**上。
      早先的做法是只取其中一条,结果
        MC 那批: 片段里只有解说,丢了游戏原声
        RDR 那批: 片段里只有游戏原声,解说只放在参考轨
      用户要求:两条都要在成片里呈现。

难点:解说的录制电平远低于游戏原声(大镖客实测中位差 32 dB,范围 -5 ~ 51 dB),
      固定增益不可行 —— 必须逐文件按实测电平自动计算。

做法(全自动,无需人工试听):
  1. 分别测两轨的中位电平(RMS 的 2 秒窗口中位数)
  2. 给解说一个"目标电平",算出差值作为增益;并夹在合理范围内
  3. 对解说做温和压缩(压缩比 + 门限),把提升后的动态压住,避免忽大忽小
  4. 游戏原声只做轻微衰减(给解说让出空间),不做压缩(保留音效冲击)
  5. 混音后做一次峰值保护(限幅到 -1 dBFS 以内)

输出:单条 48kHz 立体声 AAC,可直接进剪映片段。

用法:
  python mix_audio.py --game g.wav --voice v.wav --out mixed.m4a
                      [--voice-target -12] [--game-gain -3] [--no-denoise]
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


def median_level_db(path: str, win_sec: float = 2.0, limit: float | None = 900.0) -> float:
    """返回 RMS 的逐窗中位数(dBFS)。用中位而非均值,避免个别爆音拉高估计。"""
    cmd = [FFMPEG, "-hide_banner", "-nostats"]
    if limit:
        cmd += ["-t", str(limit)]
    cmd += ["-i", path, "-ac", "1", "-ar", "8000", "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    x = np.frombuffer(r.stdout, dtype=np.float32)
    if x.size < 8000:
        return -99.0
    st = int(8000 * win_sec)
    n = x.size // st
    if n == 0:
        return -99.0
    y = x[: n * st].reshape(n, st)
    ms = np.einsum("ij,ij->i", y, y, dtype=np.float64) / st
    db = 20 * np.log10(np.maximum(np.sqrt(ms), 1e-9))
    return float(np.median(db))


def build_filter(game_gain: float, voice_gain: float, denoise: bool,
                 compress: bool, target_lufs: float | None) -> tuple[str, str]:
    """
    构造 ffmpeg 滤镜链。返回 (游戏链, 解说链)。
    解说链的顺序很关键:降噪 -> 增益 -> 压缩 -> 限幅。
    先降噪再增益 —— 否则提升 30 dB 后底噪会被放得很大。
    """
    g = [f"volume={game_gain:.1f}dB"]
    v = []
    if denoise:
        # afftdn 是频域降噪,对稳态底噪(风扇/电流声)有效
        v.append("afftdn=nf=-25:nr=12")
    v.append(f"volume={voice_gain:.1f}dB")
    if compress:
        # 提升后动态会变大,压一下让说话音量均匀
        v.append("acompressor=threshold=-18dB:ratio=3:attack=8:release=250:makeup=1")
    # 消除提升后可能出现的低频轰鸣
    v.append("highpass=f=90")
    if target_lufs:
        v.append(f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11")
    return ",".join(g), ",".join(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--voice", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice-target", type=float, default=-14.0,
                    help="解说的目标中位电平(dBFS)。默认 -14,比游戏原声略高以保证听得清")
    ap.add_argument("--game-gain", type=float, default=-3.0,
                    help="游戏原声的增益(dB)。默认 -3,给解说让出空间")
    ap.add_argument("--max-boost", type=float, default=40.0,
                    help="解说最大提升量(dB),防止把纯底噪放大过头")
    ap.add_argument("--no-denoise", action="store_true")
    ap.add_argument("--no-compress", action="store_true")
    ap.add_argument("--target-lufs", type=float, default=None,
                    help="可选:整体响度归一化目标(EBU R128)")
    ap.add_argument("--bitrate", default="192k")
    a = ap.parse_args()

    for p in (a.game, a.voice):
        if not os.path.exists(p):
            print(f"文件不存在: {p}", file=sys.stderr)
            return 2

    lg = median_level_db(a.game)
    lv = median_level_db(a.voice)
    raw_boost = a.voice_target - lv
    boost = max(0.0, min(raw_boost, a.max_boost))
    capped = raw_boost > a.max_boost

    print(f"游戏原声 中位 {lg:6.1f} dBFS")
    print(f"解说     中位 {lv:6.1f} dBFS")
    print(f"计算提升 {raw_boost:6.1f} dB" + (f"  → 夹到 {boost:.1f} dB(上限)" if capped else ""))
    print(f"游戏增益 {a.game_gain:6.1f} dB")

    gchain, vchain = build_filter(a.game_gain, boost, not a.no_denoise,
                                  not a.no_compress, a.target_lufs)
    print(f"滤镜 游戏: {gchain}")
    print(f"滤镜 解说: {vchain}")

    fc = (f"[0:a]{gchain}[g];[1:a]{vchain}[v];"
          f"[g][v]amix=inputs=2:duration=longest:normalize=0[m];"
          f"[m]alimiter=limit=0.89:level=disabled[out]")
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-i", a.game, "-i", a.voice,
           "-filter_complex", fc, "-map", "[out]",
           "-c:a", "aac", "-b:a", a.bitrate, "-ar", "48000", "-ac", "2", a.out]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not os.path.exists(a.out):
        print(f"混音失败: {(r.stderr or '')[-400:]}", file=sys.stderr)
        return 1

    lm = median_level_db(a.out)
    print(f"\n输出 {a.out}  ({os.path.getsize(a.out) / 1e6:.1f} MB)  混音后中位 {lm:.1f} dBFS")
    meta = {"game": a.game, "voice": a.voice, "out": a.out,
            "game_level_db": round(lg, 2), "voice_level_db": round(lv, 2),
            "voice_boost_db": round(boost, 2), "raw_boost_db": round(raw_boost, 2),
            "capped": capped, "game_gain_db": a.game_gain,
            "mixed_level_db": round(lm, 2)}
    with open(a.out + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
