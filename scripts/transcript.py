"""
transcript.py —— 文稿加载 + 时间轴规整(所有选段类脚本的唯一入口)

★ 为什么需要这一层(2026-11 影之刃实战踩坑):

  whisper 在**稀疏对白**（动作游戏只有过场才说话）上会出现「句子被拉长」：
  一小句台词被标记成跨越几分钟 —— 实测有 3 个词跨 402 秒、单句最长 931 秒。
  这些虚假时长会直接污染下游：

    · pick.py 的「候选单元时长」跟着虚长 -> 选出 181s/491s 的候选段
    · tighten.py 以为片段里全是静音 -> 压缩率失控
    · 分集时长统计错误

  词级时间戳也救不了 —— 上面的 402 秒里 3 个词的 start/end 本身就被拉长了。
  所以只能用「字数反推合理时长」做上界裁剪：

      期望时长 ≈ 字数 × 0.45 秒 + 1.0(中文口播约 3.5 字/秒，留余量)
      实际时长 > 期望 × 1.6 且 > 4 秒  ->  认为被拉长，结尾收到 起点+期望

  这是保守裁剪：正常语速的句子不会被误伤（60 字的真长句期望 28 秒，20 秒的实际时长
  不会被裁），只有明显异常的才动。

用法:
    import transcript
    tr, segs = transcript.load(path)          # segs 已规整
    tr, segs = transcript.load(path, clamp=False)   # 需要原始时间轴时
"""
from __future__ import annotations

import json

# 中文口播约 3.5~4.5 字/秒;取 0.45 秒/字(偏慢)保证只裁明显异常的
SEC_PER_CHAR = 0.45
MIN_EXPECT = 1.2
CLAMP_FACTOR = 1.6
CLAMP_MIN_DUR = 4.0
MAX_DUR = 30.0          # 绝对上限:超过它一定是异常(剧本类的整段独白也不该这样)


def expected_dur(text: str) -> float:
    return max(MIN_EXPECT, len([c for c in text if c.strip()]) * SEC_PER_CHAR + 1.0)


def clamp_segments(segs: list[dict], verbose: bool = False) -> list[dict]:
    """就地规整句子的 start/end,返回统计信息 (segs, stats)。"""
    n_clamped = 0
    saved = 0.0
    for s in segs:
        t0, t1 = float(s["start"]), float(s["end"])
        dur = t1 - t0
        text = s.get("text") or ""
        exp = expected_dur(text)
        limit = max(CLAMP_MIN_DUR, exp * CLAMP_FACTOR)
        if dur > limit or dur > MAX_DUR:
            new_end = t0 + min(dur, max(exp, MIN_EXPECT))
            saved += dur - (new_end - t0)
            s["end"] = round(new_end, 3)
            s["clamped_from"] = round(dur, 3)
            n_clamped += 1
    if verbose:
        print(f"[transcript] 规整 {n_clamped} 句异常时长,回收 {saved:.0f} 秒虚假时间")
    return segs


def load(path: str, clamp: bool = True, verbose: bool = False) -> tuple[dict, list[dict]]:
    d = json.load(open(path, encoding="utf-8"))
    segs = d.get("segments") if isinstance(d, dict) else d
    segs = segs or []
    if clamp:
        clamp_segments(segs, verbose=verbose)
    return d, segs
