"""
classify_voice.py —— 判断解说轨是否「有效」

用户反馈的关键点:声音明显很小的轨,通常不是有效解说,不该硬混进成片,
而应该**明确报告**让用户决定(重录 / 单独处理 / 放弃)。

判据不看"能 ASR 出几个字"(再安静的轨噪声也能骗出文本),而是看**录音质量**:

  语音电平 (speech_dbfs)   有效解说应 >= -40 dBFS。低于 -55 基本是环境噪声级,
                           提升几十 dB 才能听见 —— 那时底噪同样被放大,听感很差。
  信噪比   (snr_db)        说话段与静默段的电平差。>= 25 dB 可用;15~25 勉强;
                           < 15 说明语音淹没在底噪里,提升后只会得到"更大的噪声"。
  覆盖率   (active_ratio)  >-45dB 的时间占比。太低说明整场几乎没说话。

三档结论:
  GOOD      可直接用,能与其他轨平顺混音
  MARGINAL  能听见但底噪明显,建议单独一条轨(让用户自己决定要不要)
  UNUSABLE  不要进成片,只报告
  
用法:
  python classify_voice.py --work <工作目录> [--out voice_quality.json]
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

# 判据阈值(可调)
SPEECH_GOOD = -40.0      # 语音电平 >= 此值算够响
SPEECH_MIN = -55.0       # 低于此值视为环境噪声级
SNR_GOOD = 25.0
SNR_MIN = 15.0
ACTIVE_GOOD = 0.25       # 说话时间占比


def analyze(path: str, limit: float = 900.0) -> dict | None:
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-t", str(limit), "-i", path,
           "-ac", "1", "-ar", "8000", "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    x = np.frombuffer(r.stdout, dtype=np.float32)
    if x.size < 16000:
        return None
    st = 8000            # 1 秒窗口
    n = x.size // st
    y = x[: n * st].reshape(n, st)
    ms = np.einsum("ij,ij->i", y, y, dtype=np.float64) / st
    db = 20 * np.log10(np.maximum(np.sqrt(ms), 1e-9))

    speech = float(np.percentile(db, 90))     # 语音电平(用 p90,排除个别爆音)
    noise = float(np.percentile(db, 10))      # 底噪(p10)
    snr = speech - noise
    active = float((db > -45).mean())
    return {"speech_dbfs": round(speech, 1), "noise_dbfs": round(noise, 1),
            "snr_db": round(snr, 1), "active_ratio": round(active, 3),
            "n_win": int(n)}


def classify(m: dict) -> tuple[str, str]:
    """返回 (等级, 理由)。"""
    s, snr, act = m["speech_dbfs"], m["snr_db"], m["active_ratio"]
    if act < 0.05:
        return "UNUSABLE", "整场几乎没有语音活动"
    if s < SPEECH_MIN:
        return "UNUSABLE", f"语音电平 {s:.0f} dBFS,属环境噪声级(需提升 {abs(s) + 14:.0f} dB 才能听见)"
    if snr < SNR_MIN:
        return "UNUSABLE", f"信噪比仅 {snr:.0f} dB,语音淹没在底噪里"
    if s < SPEECH_GOOD or snr < SNR_GOOD:
        return "MARGINAL", (f"语音 {s:.0f} dBFS / 信噪比 {snr:.0f} dB,"
                            f"能听见但底噪明显")
    return "GOOD", f"语音 {s:.0f} dBFS / 信噪比 {snr:.0f} dB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=float, default=900.0, help="每文件最多分析多少秒")
    a = ap.parse_args()

    files = sorted(f for f in os.listdir(a.work) if f.endswith(".voice.wav"))
    print(f"发现 {len(files)} 条解说轨\n")
    print(f"{'文件':<26} {'语音dBFS':>9} {'底噪dBFS':>9} {'信噪比':>7} {'覆盖':>6}  判定")
    print("-" * 92)
    rows = []
    for f in files:
        base = f[: -len(".voice.wav")]
        m = analyze(os.path.join(a.work, f), a.limit)
        if not m:
            print(f"{base:<26} 分析失败")
            continue
        level, why = classify(m)
        icon = {"GOOD": "✅", "MARGINAL": "⚠️ ", "UNUSABLE": "❌"}[level]
        print(f"{base:<26} {m['speech_dbfs']:>9.1f} {m['noise_dbfs']:>9.1f} "
              f"{m['snr_db']:>7.1f} {m['active_ratio'] * 100:>5.0f}%  {icon} {level}")
        rows.append({"file": base, **m, "verdict": level, "reason": why})

    print("-" * 92)
    for lv in ("GOOD", "MARGINAL", "UNUSABLE"):
        sub = [r for r in rows if r["verdict"] == lv]
        if not sub:
            continue
        print(f"\n{lv} ({len(sub)} 个):")
        for r in sub:
            print(f"  {r['file']:<26} {r['reason']}")

    op = a.out or os.path.join(a.work, "voice_quality.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\n-> {op}")

    n_ok = sum(1 for r in rows if r["verdict"] == "GOOD")
    print(f"\n结论:{n_ok}/{len(rows)} 条解说轨可直接用;"
          f"{sum(1 for r in rows if r['verdict'] != 'GOOD')} 条建议单独处理或放弃")


if __name__ == "__main__":
    raise SystemExit(main())
