"""
topics.py —— TextTiling 话题分段(把长对话切成"一场戏/一个话题")

参考:Hearst, M. A. (1997). Text Tiling: Segmenting Text into Multi-paragraph
Subtopic Passages. Computational Linguistics 23(1):33–64.
https://aclanthology.org/J97-1003/

为什么要它(2026-11 影之刃实战):
  按"时长上限"强行切长对话,会把一段连续的戏切成前后两半,观众感觉"话没说完就跳"。
  TextTiling 用**词汇衔接度**找话题转折:相邻两段话用词重合度突然下降的地方 = 换话题,
  在那里切才是"一场戏说完"。

中文实现要点:
  · 没有分词器也能用:取**汉字的字 bigram** 当伪词(实测对话题转折足够敏感)
  · 两边各取 k 句做"块",算词频向量的余弦相似度;对每个间隙算 depth
    (左右局部峰与谷的落差),depth 越大越像话题边界
  · 阈值用 depth 分布的 mean + 0.5*std(与原论文一致的启发式)

用法:
  python topics.py --transcript t.json [--block 6] [--min-gap 8] [--out t.topics.json]
或在代码里:
  import topics
  bnd = topics.boundaries(segs)          # 返回话题边界(秒)列表
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys

CJK = re.compile(r"[\u4e00-\u9fff]")
PUNCT = re.compile(r"[\s，。、！？；：,.!?;:\"'“”‘’（）()【】\[\]…—\-~]+")


def tokens_of(text: str) -> list[str]:
    """中文用字 bigram 当伪词;英文/数字保留原词(小写)。"""
    toks: list[str] = []
    for m in re.finditer(r"[A-Za-z0-9]+", text):
        toks.append(m.group(0).lower())
    chars = CJK.findall(text)
    for i in range(len(chars) - 1):
        toks.append(chars[i] + chars[i + 1])
    if len(chars) == 1:
        toks.append(chars[0])
    return toks


def _vec(toks: list[str]) -> dict[str, float]:
    v: dict[str, float] = {}
    for t in toks:
        v[t] = v.get(t, 0.0) + 1.0
    n = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {k: x / n for k, x in v.items()}


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(k, 0.0) for k, x in a.items())


def _depth(sim: list[float], i: int, span: int) -> float:
    """i 处的深度分:左右各找局部峰,算 (左峰 - 谷) + (右峰 - 谷)。"""
    v = sim[i]
    left = v
    for j in range(i - 1, max(-1, i - span - 1), -1):
        if sim[j] > left:
            left = sim[j]
        if sim[j] > v and sim[j] > sim[j - 1] if j > 0 else False:
            break
    right = v
    for j in range(i + 1, min(len(sim), i + span + 1)):
        if sim[j] > right:
            right = sim[j]
        if sim[j] > v:
            break
    return max(0.0, (left - v) + (right - v))


def boundaries(segs: list[dict], block: int = 6, span: int = 3,
               min_gap: float = 8.0, min_sentences: int = 8) -> list[dict]:
    """返回话题边界列表 [{t, depth}]。segs 需带 start/end/text,按时间排序。"""
    segs = [s for s in segs if (s.get("text") or "").strip()]
    if len(segs) < min_sentences:
        return []
    toks = [tokens_of(s["text"]) for s in segs]
    sims: list[float] = []
    for g in range(1, len(segs)):
        left = [t for blk in toks[max(0, g - block):g] for t in blk]
        right = [t for blk in toks[g:min(len(segs), g + block)] for t in blk]
        if not left or not right:
            sims.append(0.0)
            continue
        sims.append(_cos(_vec(left), _vec(right)))

    depths = [_depth(sims, i, span) for i in range(len(sims))]
    if not depths:
        return []
    mean = sum(depths) / len(depths)
    var = sum((d - mean) ** 2 for d in depths) / len(depths)
    thr = mean + 0.5 * math.sqrt(var)

    out: list[dict] = []
    for i, d in enumerate(depths):
        if d < thr or d <= 0.02:
            continue
        # 边界落在第 i 句与第 i+1 句之间(i 对应 sims 下标 -> segs[i] 与 segs[i+1])
        if i + 1 >= len(segs):
            continue
        t = (segs[i]["end"] + segs[i + 1]["start"]) / 2
        gap = segs[i + 1]["start"] - segs[i]["end"]
        if gap < 0:
            continue
        out.append({"t": round(t, 2), "depth": round(d, 4), "gap": round(gap, 2)})
    # 合并过近的边界(默认 >= min_gap 秒才保留,优先保留 depth 大的)
    out.sort(key=lambda x: -x["depth"])
    kept: list[dict] = []
    for b in out:
        if all(abs(b["t"] - k["t"]) >= min_gap for k in kept):
            kept.append(b)
    kept.sort(key=lambda x: x["t"])
    return kept


def in_range(segs: list[dict], t0: float, t1: float) -> list[dict]:
    return [s for s in segs if s["start"] >= t0 - 0.5 and s["end"] <= t1 + 0.5]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--block", type=int, default=6)
    ap.add_argument("--min-gap", type=float, default=8.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d = json.load(open(a.transcript, encoding="utf-8"))
    segs = d["segments"]
    bnd = boundaries(segs, block=a.block, min_gap=a.min_gap)
    print(f"台词 {len(segs)} 句 -> 话题边界 {len(bnd)} 个")
    for b in bnd[:20]:
        print(f"  {int(b['t'])//60:>3}:{int(b['t'])%60:02d}  depth={b['depth']:.3f} gap={b['gap']:.1f}s")
    op = a.out or os.path.splitext(a.transcript)[0] + ".topics.json"
    json.dump({"boundaries": bnd}, open(op, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
