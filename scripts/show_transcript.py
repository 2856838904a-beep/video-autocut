"""
show_transcript.py —— 人肉抽查转写结果(判断解说轨是不是挑错了)

转写跑完后先看一眼:句数太少、语言不对、全是重复短句,说明解说轨挑错了。
比"等生成完草稿才发现内容不对"便宜得多。

用法:
    python show_transcript.py <transcript.json>
"""
import json
import statistics
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

if len(sys.argv) < 2:
    print(__doc__)
    raise SystemExit(2)

p = sys.argv[1]
j = json.load(open(p, encoding="utf-8"))
m = j["meta"]
segs = j["segments"]

print("=== meta ===")
for k, v in m.items():
    print(f"  {k} = {v}")

print(f"\n=== 共 {len(segs)} 句 ===")
print("--- 前 10 句 ---")
for s in segs[:10]:
    print(f"  [{s['start']:8.1f}-{s['end']:8.1f}] "
          f"lp={s.get('avg_logprob', 0):6.2f} ns={s.get('no_speech_prob', 0):5.2f}  "
          f"{s['text'][:64]}")
print("--- 中间抽样 10 句 ---")
mid = segs[len(segs) // 2: len(segs) // 2 + 10]
for s in mid:
    print(f"  [{s['start']:8.1f}-{s['end']:8.1f}] "
          f"lp={s.get('avg_logprob', 0):6.2f} ns={s.get('no_speech_prob', 0):5.2f}  "
          f"{s['text'][:64]}")
print("--- 最后 6 句 ---")
for s in segs[-6:]:
    print(f"  [{s['start']:8.1f}-{s['end']:8.1f}] "
          f"lp={s.get('avg_logprob', 0):6.2f} ns={s.get('no_speech_prob', 0):5.2f}  "
          f"{s['text'][:64]}")

chars = sum(len(s["text"]) for s in segs)
dur = m.get("duration_sec") or (segs[-1]["end"] if segs else 1)
print(f"\n总字数 {chars}   时长 {dur / 60:.1f} 分钟   语速 {chars / dur * 60:.1f} 字/分钟")

# 语音覆盖率
spoken = sum(s["end"] - s["start"] for s in segs)
print(f"有语音时长 {spoken / 60:.1f} 分钟  覆盖率 {spoken / dur * 100:.1f}%")

low = [s for s in segs if s.get("avg_logprob", 0) < -1.0]
hall = [s for s in segs if s.get("no_speech_prob", 0) > 0.6]
print(f"低置信度句(avg_logprob<-1.0): {len(low)} ({len(low) / max(1, len(segs)) * 100:.1f}%)")
print(f"疑似幻觉句(no_speech_prob>0.6): {len(hall)}")

# 最长静默间隙(解说空档 = 可能挂机)
gaps = []
for i in range(1, len(segs)):
    g = segs[i]["start"] - segs[i - 1]["end"]
    if g > 15:
        gaps.append((g, segs[i - 1]["end"], segs[i]["start"]))
gaps.sort(reverse=True)
print(f"\n解说空档 >15 秒: {len(gaps)} 处,最长 8 处:")
for g, a, b in gaps[:8]:
    print(f"   {a / 60:6.1f} 分钟处 空档 {g:6.1f}s")

if segs:
    print(f"\n平均句长 {statistics.mean(s['end'] - s['start'] for s in segs):.2f}s")
