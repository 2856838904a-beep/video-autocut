"""
export_final.py
    把每集"选段清单"直接渲染成**平台可发布的成片**,并自动做响度标准化与平台适配。

★ 为什么自己渲染(2026-11 影之刃实战结论):
  剪映专业版**没有可用的导出 CLI**(自带的 jianying-agent-cli 只有 draft edit/extract/
  validate-patch,没有 export;GUI 导出无法可靠自动化)。所以发布链路不从剪映取值,
  而是从同一份选段清单(cuts)直接出片 —— 好处是:批量、可复现、可精确控制
  响度/码率/分辨率/竖屏适配。

  ⚠️ 注意:这意味着**不含你在剪映里手动加的转场/文字**。若在剪映里改过,
     用 `--source files` 模式:手工从剪映导出 mp4,再交给本脚本做响度与平台适配。

★ 响度标准化(为什么必须做):
  游戏原声 + 解说的动态范围很大,平台会二次响度归一 —— 你给 -14 LUFS 它才不会被压扁。
  默认 target=-14 LUFS / TP=-1.5 dBTP / LRA=11(哔哩哔哩、抖音、YouTube 都在 -14 左右)。

★ 平台预设(可按需在 PRESETS 里改):

  平台       分辨率          帧率   编码           码率/质量      响度     竖屏
  douyin     1080x1920(9:16) 30     h264           8 Mbps CBR-ish  -14     是(模糊背景填充)
  bilibili   1920x1080        60     h264           10 Mbps         -14     否
  youtube    1920x1080        60     h264           12 Mbps         -14     否
  archive    源分辨率         源     hevc crf18     高质量          -16     否

用法:
  # 从选段清单渲染(默认输出到素材目录下 _autocut_export/<时间戳>/)
  python export_final.py --episodes _episodes_beats/episodes.json \
         --preset douyin,bilibili,youtube,archive [--only 1,2] [--loudnorm single|two-pass]

  # 手工导出后只做响度+平台适配
  python export_final.py --source files --input "G:\\...\\我的导出.mp4" --preset douyin
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

FF = paths.ffmpeg()
FP = paths.ffprobe()

# ---------------------------------------------------------------- 平台预设
PRESETS: dict[str, dict] = {
    "douyin": {
        "label": "抖音",
        "width": 1080, "height": 1920, "fps": 30,
        "vcodec": "h264", "encoder": "nvenc", "vbitrate_mbps": 8.0,
        "acodec": "aac", "abitrate": "192k",
        "lufs": -13.0, "tp": -2.0, "lra": 11.0, "loudnorm": "linear_limiter",
        "vertical": True, "vertical_mode": "blur",
    },
    "bilibili": {
        "label": "B站",
        "width": 1920, "height": 1080, "fps": 60,
        "vcodec": "h264", "encoder": "nvenc", "vbitrate_mbps": 10.0,
        "acodec": "aac", "abitrate": "192k",
        "lufs": -13.0, "tp": -2.0, "lra": 11.0, "loudnorm": "linear_limiter",
        "vertical": False,
    },
    "youtube": {
        "label": "YouTube",
        "width": 1920, "height": 1080, "fps": 60,
        "vcodec": "h264", "encoder": "nvenc", "vbitrate_mbps": 12.0,
        "acodec": "aac", "abitrate": "192k",
        "lufs": -13.0, "tp": -2.0, "lra": 11.0, "loudnorm": "linear_limiter",
        "vertical": False,
    },
    "archive": {
        "label": "归档",
        "width": 0, "height": 0, "fps": 0,          # 0 = 跟随源
        "vcodec": "hevc", "encoder": "nvenc", "crf": 20,
        "acodec": "aac", "abitrate": "192k",
        "lufs": -15.0, "tp": -2.5, "lra": 13.0, "loudnorm": "linear_limiter",
        "vertical": False,
    },
}


def probe(path: str) -> dict:
    j = paths.probe_json("-show_streams", "-show_format", path)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), {})
    fr = v.get("r_frame_rate") or "30/1"
    try:
        num, den = fr.split("/")
        fps = float(num) / float(den or 1)
    except Exception:
        fps = 30.0
    return {"dur": float(j["format"].get("duration") or 0),
            "w": int(v.get("width") or 0), "h": int(v.get("height") or 0),
            "fps": round(fps, 3), "vcodec": v.get("codec_name"),
            "acodec": a.get("codec_name"), "has_audio": bool(a)}


def resolve_size(p: dict, src: dict) -> tuple[int, int, int]:
    """按预设与源分辨率算输出尺寸/帧率;不放大、不改变宽高比(竖屏另处理)。"""
    w = p["width"] or src["w"]
    h = p["height"] or src["h"]
    fps = int(p["fps"] or src["fps"] or 30)
    if not p["width"]:                       # 归档:跟随源,但宽高取偶数
        w = src["w"] - src["w"] % 2
        h = src["h"] - src["h"] % 2
    return w, h, fps


def build_filter(clips: list[dict], srcs: list[str], p: dict,
                 src_meta: list[dict]) -> tuple[str, str, str]:
    """构造 trim+concat 的 filter_complex,返回 (filter, vmap, amap)。"""
    chains, vparts, aparts = [], [], []
    for i, (c, meta) in enumerate(zip(clips, src_meta)):
        ss, ee = float(c["start"]), float(c["end"])
        chains.append(f"[{i}:v]trim=start={ss:.3f}:end={ee:.3f},setpts=PTS-STARTPTS[v{i}]")
        chains.append(f"[{i}:a]atrim=start={ss:.3f}:end={ee:.3f},asetpts=PTS-STARTPTS[a{i}]")
        vparts.append(f"[v{i}]")
        aparts.append(f"[a{i}]")
    vconcat = "".join(vparts) + f"concat=n={len(clips)}:v=1:a=0[vcat]"
    aconcat = "".join(aparts) + f"concat=n={len(clips)}:v=0:a=1[acat]"
    chains += [vconcat, aconcat]

    w, h, fps = p["out_w"], p["out_h"], p["out_fps"]
    if p.get("vertical"):
        # 竖屏:游戏画面按宽度等比缩放,背景用同帧模糊填充(不裁掉左右内容)
        mode = p.get("vertical_mode", "blur")
        if mode == "crop":
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                  f"crop={w}:{h},fps={fps}")
            chains.append(f"[vcat]{vf}[vout]")
        else:
            chains.append(
                f"[vcat]split=2[bg][fg];"
                f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},scale={max(2,w//4)}:{max(2,h//4)},boxblur=6:1,"
                f"scale={w}:{h}[bgb];"
                f"[fg]scale={w}:-2[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,fps={fps}[vout]")
    else:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
        chains.append(f"[vcat]{vf}[vout]")

    vmap = "[vout]"
    lufs, tp, lra = p["lufs"], p["tp"], p["lra"]
    if p.get("loudnorm_second"):
        chains.append(f"[acat]loudnorm=I={lufs}:TP={tp}:LRA={lra}[aout]")
    elif p.get("loudnorm_linear"):
        chains.append(f"[acat]loudnorm=I={lufs}:TP={tp}:LRA={lra}:linear=true[aout]")
    else:
        chains.append(f"[acat]loudnorm=I={lufs}:TP={tp}:LRA={lra}[aout]")
    amap = "[aout]"
    return ";".join(chains), vmap, amap


def measure_loudnorm(clips: list[dict], lufs: float, tp: float,
                     lra: float) -> dict | None:
    """第一遍:量出拼接后音频的响度参数(只解码音频,不解视频,很快)。

    为什么必须量:loudnorm 不传 measured_* 时会走**动态模式**,实测输出 I=-13.4(目标 -14)、
    真峰 +1.0 dBFS —— 平台会削波。两遍法(measure -> linear 应用)才能准确命中目标。
    """
    cmd = [FF, "-hide_banner", "-nostats"]
    chains, aparts = [], []
    for i, c in enumerate(clips):
        ss = float(c["start"])
        dur = float(c["end"]) - ss
        cmd += ["-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", c["_src"]]
        chains.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")
        aparts.append(f"[a{i}]")
    n = len(clips)
    chains.append("".join(aparts) + f"concat=n={n}:v=0:a=1[acat]")
    chains.append(f"[acat]loudnorm=I={lufs}:TP={tp}:LRA={lra}:print_format=json[an]")
    cmd += ["-filter_complex", ";".join(chains), "-map", "[an]", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    txt = r.stderr or ""
    import re as _re
    m = _re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, _re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def encode_cmd(clips: list[dict], p: dict, out: str, loudnorm: str,
               measured: dict | None = None) -> list[str]:
    """构造 ffmpeg 命令。

    ★ 切点用**输入级 seek**(-ss/-t 放在 -i 前)+ filter 里只做 setpts 归零。
      早期写成"输入级 seek + trim(start=绝对秒数)"会双重裁剪,切点全错。
      -ss 放在 -i 前是快速定位(关键帧),配合重编码可精确到帧,速度比从 0 解码快一个量级。
    """
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y"]
    chains, vparts, aparts = [], [], []
    for i, c in enumerate(clips):
        ss = float(c["start"])
        dur = float(c["end"]) - ss
        cmd += ["-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", c["_src"]]
        chains.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}]")
        chains.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")
        vparts.append(f"[v{i}]")
        aparts.append(f"[a{i}]")
    n = len(clips)
    chains.append("".join(vparts) + f"concat=n={n}:v=1:a=0[vcat]")
    chains.append("".join(aparts) + f"concat=n={n}:v=0:a=1[acat]")

    w, h, fps = p["out_w"], p["out_h"], p["out_fps"]
    if p.get("vertical"):
        if p.get("vertical_mode") == "crop":
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                  f"crop={w}:{h},fps={fps},setsar=1")
            chains.append(f"[vcat]{vf}[vout]")
        else:
            chains.append(
                f"[vcat]split=2[bg][fg];"
                f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},scale={max(2,w//4)}:{max(2,h//4)},boxblur=6:1,"
                f"scale={w}:{h}[bgb];"
                f"[fg]scale={w}:-2[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,fps={fps},setsar=1[vout]")
    else:
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}")
        chains.append(f"[vcat]{vf}[vout]")

    lufs, tp, lra = p["lufs"], p["tp"], p["lra"]
    mode = p.get("loudnorm") or loudnorm
    if measured and mode != "single" and mode != "dynamic":
        ln = (f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:linear=true"
              f":measured_I={measured.get('input_i')}"
              f":measured_TP={measured.get('input_tp')}"
              f":measured_LRA={measured.get('input_lra')}"
              f":measured_thresh={measured.get('input_thresh')}"
              f":offset={measured.get('target_offset')}")
    else:
        ln = f"loudnorm=I={lufs}:TP={tp}:LRA={lra}"
    # ★ 限幅器兜底,但必须 level=false:
    #   alimiter 的 level 选项默认 true(自动电平),会整体再抬 2~3 dB ——
    #   实测加上默认限幅器后输出从 -14 变成 -11.8 LUFS、真峰 +1.5 dBFS,越修越响。
    if mode == "dynamic":
        chains.append(f"[acat]{ln}[aout]")
    else:
        limit = 10 ** (tp / 20.0)
        chains.append(f"[acat]{ln},alimiter=level=false:limit={limit:.4f}:"
                      f"attack=1:release=50[aout]")

    cmd += ["-filter_complex", ";".join(chains), "-map", "[vout]", "-map", "[aout]"]
    nvenc = p.get("encoder") == "nvenc"
    if p["vcodec"] == "hevc":
        if nvenc:
            cmd += ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr",
                    "-cq", str(p.get("crf", 20)), "-b:v", "0", "-tag:v", "hvc1"]
        else:
            cmd += ["-c:v", "libx265", "-crf", str(p.get("crf", 18)),
                    "-preset", "medium", "-tag:v", "hvc1"]
    else:
        if nvenc:
            cmd += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
                    "-b:v", f"{p['vbitrate_mbps']}M",
                    "-maxrate", f"{p['vbitrate_mbps'] * 1.4:.2f}M",
                    "-bufsize", f"{p['vbitrate_mbps'] * 2:.2f}M"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "medium",
                    "-b:v", f"{p['vbitrate_mbps']}M",
                    "-maxrate", f"{p['vbitrate_mbps'] * 1.4:.2f}M",
                    "-bufsize", f"{p['vbitrate_mbps'] * 2:.2f}M",
                    "-profile:v", "high", "-level", "4.2"]
    cmd += ["-pix_fmt", "yuv420p", "-c:a", p["acodec"], "-b:a", p["abitrate"],
            "-ar", "48000", "-movflags", "+faststart", out]
    return cmd


def measure_loudness(path: str) -> dict:
    """用 ebur128 量输出响度,验证 loudnorm 是否到位。"""
    r = subprocess.run([FF, "-hide_banner", "-nostats", "-i", path,
                        "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    txt = (r.stderr or "")
    out = {}
    for key, pat in (("I", "I:"), ("TP", "Peak:")):
        pass
    import re
    m = re.findall(r"I:\s*(-?\d+\.\d+)\s*LUFS", txt)
    if m:
        out["integrated_lufs"] = float(m[-1])
    m = re.findall(r"Peak:\s*(-?\d+\.\d+)\s*dBFS", txt)
    if m:
        out["true_peak_dbfs"] = float(m[-1])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", help="episodes.json(推荐)")
    ap.add_argument("--source", choices=["cuts", "files"], default="cuts")
    ap.add_argument("--input", action="append", default=[], help="source=files 时的输入 mp4")
    ap.add_argument("--preset", default="douyin,bilibili,youtube,archive")
    ap.add_argument("--only", default="", help="只做某几集,如 1,2")
    ap.add_argument("--src-dir", default=None,
                    help="原始录像目录(默认取环境变量 AUTOCUT_SRC_DIR)")
    ap.add_argument("--out-root", default=None,
                    help="默认 <素材目录>/_autocut_export/<时间戳>/")
    ap.add_argument("--loudnorm", choices=["single", "linear", "two-pass"],
                    default="linear")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    presets = [x.strip() for x in a.preset.split(",") if x.strip() in PRESETS]
    if not presets:
        print("没有可用预设", file=sys.stderr)
        return 2

    jobs: list[dict] = []      # {name, clips:[{start,end,_src}], src_dir}
    if a.source == "files":
        files = a.input
        if not files:
            print("--source files 需要 --input", file=sys.stderr)
            return 2
        for f in files:
            jobs.append({"name": os.path.splitext(os.path.basename(f))[0],
                         "clips": [{"start": 0, "end": probe(f)["dur"], "_src": os.path.abspath(f)}],
                         "src_dir": os.path.dirname(os.path.abspath(f)),
                         "is_file": True})
    else:
        ep = json.load(open(a.episodes, encoding="utf-8"))
        want = {int(x) for x in a.only.split(",") if x.strip().isdigit()} if a.only else None
        for e in ep["episodes"]:
            if want and e["episode"] not in want:
                continue
            cuts = json.load(open(e["file"], encoding="utf-8"))["cuts"]
            clips = []
            for c in cuts:
                clips.append({"start": float(c["start"]), "end": float(c["end"]),
                              "_src": c.get("_video") or c["src"]})
            jobs.append({"name": f"影之刃_EP{e['episode']:02d}",
                         "episode": e["episode"], "clips": cuts,
                         "src_dir": os.path.dirname(os.path.abspath(e["file"]))})

    src_dir = a.src_dir or os.environ.get("AUTOCUT_SRC_DIR", "")
    if a.source == "cuts" and not src_dir:
        print("需要 --src-dir(原始录像目录)", file=sys.stderr)
        return 2

    ts = time.strftime("%Y%m%d_%H%M")
    out_root = a.out_root or os.path.join(src_dir or os.getcwd(), "_autocut_export", ts)
    print(f"输出根目录: {out_root}")
    print(f"预设: {', '.join(presets)}   响度模式: {a.loudnorm}")

    manifest = {"created": ts, "out_root": out_root, "jobs": []}
    total_time = 0.0
    for job in jobs:
        for c in job["clips"]:
            if "src" in c and not c.get("_src"):
                c["_src"] = os.path.join(src_dir, c["src"] + ".mkv")
        for pname in presets:
            p = PRESETS[pname]
            src0 = probe(job["clips"][0]["_src"])
            w, h, fps = resolve_size(p, src0)
            p = dict(p, out_w=w, out_h=h, out_fps=fps)
            orient = "竖屏" if p.get("vertical") else "横屏"
            outname = f"{job['name']}_{p['label']}_{w}x{h}.mp4"
            out = os.path.join(out_root, outname)
            os.makedirs(out_root, exist_ok=True)
            dur = sum(float(c["end"]) - float(c["start"]) for c in job["clips"])
            est_mb = dur * (p.get("vbitrate_mbps", 6) + 1.0) * 0.125
            print(f"\n[{job['name']}] -> {p['label']} {w}x{h}@{fps} "
                  f"{p['vcodec']} 约 {dur/60:.1f} 分钟 / 预估 {est_mb:.0f} MB")
            if a.dry_run:
                continue
            measured = None
            mode = p.get("loudnorm") or a.loudnorm
            if mode in ("linear", "linear_limiter"):
                measured = measure_loudnorm(job["clips"], p["lufs"], p["tp"], p["lra"])
                if measured:
                    print(f"  实测输入 I={measured.get('input_i')} LUFS "
                          f"TP={measured.get('input_tp')} dBFS -> 两遍法线性应用")
                else:
                    print("  ⚠️ 响度测量失败,退回单遍动态模式")
            cmd = encode_cmd(job["clips"], p, out, a.loudnorm, measured)
            t0 = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
            dt = time.time() - t0
            total_time += dt
            if r.returncode != 0:
                print(f"  ❌ 失败 rc={r.returncode} {(r.stderr or '')[-300:]}")
                continue
            info = probe(out)
            loud = measure_loudness(out)
            mb = os.path.getsize(out) / 1e6
            print(f"  ✅ {mb:.0f} MB / {info['dur']/60:.2f} 分钟  "
                  f"响度 {loud.get('integrated_lufs', '?')} LUFS "
                  f"TP {loud.get('true_peak_dbfs', '?')} dBFS  "
                  f"{dur/max(dt,1):.1f}x 实时")
            manifest["jobs"].append({
                "episode": job.get("episode"), "platform": pname,
                "label": p["label"], "file": out, "size_mb": round(mb, 1),
                "duration_sec": round(info["dur"], 2),
                "width": info["w"], "height": info["h"], "fps": info["fps"],
                "lufs": loud.get("integrated_lufs"),
                "true_peak_dbfs": loud.get("true_peak_dbfs"),
            })

    if not a.dry_run:
        mp = os.path.join(out_root, "manifest.json")
        json.dump(manifest, open(mp, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\n-> {mp}")
        print(f"总耗时 {total_time/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
