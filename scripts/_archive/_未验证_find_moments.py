"""
find_moments.py —— 从解说文稿里挑「值得留的时段」

你的三个标准:
  情绪/气氛高点 —— 笑声、惊呼、催prompt、连珠炮对话、脏话(游戏里通常=打起来了)
  有信息量的内容 —— 教学、交易、机制讲解、坐标、装备名
  画面好看的空镜 —— 需要画面分析,这里只标出"解说空档"(可能是风景/建篔展示)

做法:对每句话打情绪分和信息分,再按时间聚合。

用法:
  python find_moments.py <transcript.json> [--top 20] [--out x.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 情绪信号(中文口语/游戏场景)
EMO = [
    (r"卧槽|我操|我草|哎哟|妈呀|天啊|靠[!！]|我去|牛逼|牛啊|太狠|离谱|绝了|笑死|哈哈哈|嘎嘎", 4),
    (r"死[了啦]|挂了|没了|完了|凉了|翻车|炸了|爆了|掉了|跑了|快跑|救[我命]|打他|上[啊呀]|撤", 3),
    (r"小心|注意|别动|危险|来了来了|他来了|小心点", 2),
    (r"[!！]{1,}｜真的假的|不会吧|居然|竟然|怎么[会能]|为什么", 1.5),
]
# 信息信号
INFO = [
    (r"怎么做|怎么弄|教你|我说一下|原因是|因为|所以|方法是|技巧|记住|机制|规则|合成|配方", 3),
    (r"坐标|位置|在哪|地图|地形|传送|矿洞|下界|末地|附魔|刷怪|红石|农[场田]|机器", 2),
    (r"多少[个块级]|几个|需要|材料|装备|武器|药水|装备|钻石|下界合金", 1.5),
]
FILL = re.compile(r"^(嗯+|啊+|哦+|呃+|那个|这个|就是|然后|对)[\s，。、]*$")


def score_sentence(t: str) -> tuple[float, float]:
    e = sum(w for pat, w in EMO if re.search(pat, t))
    i = sum(w for pat, w in INFO if re.search(pat, t))
    if FILL.match(t.strip()):
        e -= 2
        i -= 2
    if len(t) >= 12:
        e += 0.3
    return e, i


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--gap", type=float, default=25.0,
                    help="间隔小于这个秒数就并成一段")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    j = json.load(open(a.transcript, encoding="utf-8"))
    segs = j["segments"] if isinstance(j, dict) else j

    # 逐句打分
    rows = []
    for s in segs:
        e, i = score_sentence(s["text"])
        rows.append({"start": s["start"], "end": s["end"], "text": s["text"],
                     "emo": round(e, 1), "info": round(i, 1),
                     "total": round(e * 1.6 + i, 2)})
    hot = [r for r in rows if r["total"] > 0]

    # 聚合
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for r in hot:
        if cur and r["start"] - cur[-1]["end"] <= a.gap:
            cur.append(r)
        else:
            if cur:
                groups.append(cur)
            cur = [r]
    if cur:
        groups.append(cur)

    out = []
    for g in groups:
        sc = sum(x["total"] for x in g)
        dur = g[-1]["end"] - g[0]["start"]
        if dur < 3:
            continue
        out.append({
            "start": round(g[0]["start"], 1),
            "end": round(g[-1]["end"], 1),
            "dur": round(dur, 1),
            "score": round(sc / max(1, len(g)) * (1 + min(len(g), 8) / 8), 2),
            "sentences": len(g),
            "emo": round(sum(x["emo"] for x in g), 1),
            "info": round(sum(x["info"] for x in g), 1),
            "text": " ".join(x["text"] for x in g)[:220],
        })
    out.sort(key=lambda x: -x["score"])
    out = out[: a.top]

    total = j.get("meta", {}).get("duration_sec", rows[-1]["end"] if rows else 0)

    def fmt(t: float) -> str:
        t = int(t)
        return f"{t // 60:02d}:{t % 60:02d}"

    print(f"素材时长 {fmt(total)}   共 {len(segs)} 句")
    print(f"命中 {len(hot)} 句 -> 聚合 {len(groups)} 段 -> 取前 {len(out)} 段\n")
    print("=" * 78)
    for n, c in enumerate(out, 1):
        kinds = []
        if c["emo"] >= 4:
            kinds.append("情绪高")
        elif c["emo"] > 0:
            kinds.append("有情绪")
        if c["info"] >= 3:
            kinds.append("有信息")
        elif c["info"] > 0:
            kinds.append("含信息")
        tag = "/".join(kinds) or "一般"
        print(f"#{n:>2}  {fmt(c['start'])}–{fmt(c['end'])}  "
              f"时长 {c['dur']:5.1f}s  {c['sentences']:>2}句  "
              f"情绪 {c['emo']:5.1f}  信息 {c['info']:5.1f}  [{tag}]")
        print(f"     {c['text']}")
        print()

    op = a.out or os.path.splitext(a.transcript)[0] + ".moments.json"
    with open(op, "w", encoding="utf-8") as f:
        json.dump({"source": a.transcript, "duration": total, "moments": out},
                  f, ensure_ascii=False, indent=1)
    print(f"-> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
