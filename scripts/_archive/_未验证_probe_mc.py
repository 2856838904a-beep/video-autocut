"""探测 G:\\录像\\MC 全部素材的技术参数,判断选段策略。"""
import json
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

FFPROBE = paths.ffprobe()
DIR = r"G:\录像\MC"

files = sorted(
    (os.path.join(DIR, f) for f in os.listdir(DIR)
     if os.path.splitext(f)[1].lower() in (".mkv", ".mp4", ".flv", ".mov", ".avi")),
    key=lambda p: os.path.getmtime(p),
)

print(f"素材 {len(files)} 个\n")
print(f"{'文件':<26} {'时长':>9} {'分辨率':>11} {'fps':>6} {'音轨':>16} {'码率':>9} {'GB':>6}")
print("-" * 92)

rows = []
total_dur = 0.0
total_gb = 0.0
for p in files:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,r_frame_rate,channels,sample_rate,bit_rate",
         "-show_entries", "format=duration,size,bit_rate", "-of", "json", p],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        j = json.loads(r.stdout)
    except Exception:
        print(f"{os.path.basename(p):<26} 探测失败")
        continue
    fmt = j.get("format", {})
    dur = float(fmt.get("duration") or 0)
    sz = float(fmt.get("size") or os.path.getsize(p))
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    fr = v.get("r_frame_rate", "0/1")
    try:
        num, den = fr.split("/")
        fps = float(num) / float(den) if float(den) else 0
    except Exception:
        fps = 0
    br = float(fmt.get("bit_rate") or 0) / 1e6
    total_dur += dur
    total_gb += sz / 1e9
    rows.append({"path": p, "dur": dur, "fps": fps, "has_audio": a is not None})

    h = int(dur // 3600)
    m = int((dur % 3600) // 60)
    atxt = (f"{a.get('codec_name')} {a.get('channels')}ch {a.get('sample_rate')}"
            if a else "无音轨!")
    print(f"{os.path.basename(p):<26} {h:>3}:{m:02d}  "
          f"{str(v.get('width')) + 'x' + str(v.get('height')):>11} "
          f"{fps:>6.0f} {atxt:>16} {br:>7.1f}M {sz / 1e9:>6.2f}")

th = total_dur / 3600
print("-" * 92)
print(f"{'合计':<26} {int(th):>3}:{int((total_dur % 3600) // 60):02d}  "
      f"{'':>11} {'':>6} {'':>16} {'':>9} {total_gb:>6.1f}")

with_audio = sum(1 for r in rows if r["has_audio"])
print(f"\n有音轨的素材: {with_audio}/{len(rows)}")
print(f"总时长: {th:.2f} 小时 ({total_dur:.0f} 秒)")
print(f"总大小: {total_gb:.1f} GB")
print(f"\n分辨率分布: {sorted({(r['dur'], ) for r in rows}) and sorted({round(r['fps']) for r in rows})} fps")
