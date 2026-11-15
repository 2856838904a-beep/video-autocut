r"""
rebuild_separate.py —— 最终重建:游戏原声与游玩解说分成两条轨

⚠️ **批次专用脚本,不是通用工具。**
   下面的 BATCHES 配置写死了本批次的素材/片段/音频路径。
   换批次要改配置。通用管线见 SKILL.md「固定流程」。
   音轨结构规则(为什么不能只给 9 个字段)见 references/gotchas.md 第 0c 条。

最终形态(用户要求):
  视频轨   ← 每段只含游戏原声
  音频轨「游玩解说」← 每段对应时间窗的解说(单独一条,可独立调音量/静音)

空间安排(两个盘都紧张,必须想清楚):
  新片段 -> G 盘(同卷硬链接,零额外占用)
  总草稿 -> C 盘草稿库(直接复制,占 47.9 GB;C 盘 87.8 GB 够)
  旧片段 -> 全部完成后删除,回收空间

用法:
  python rebuild_separate.py --batch MC|RDR --plan _rebuild_plan.json
                             [--only 子串] [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
JY_ROOT = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                       r"JianyingPro\User Data\Projects\com.lveditor.draft")

CFG = {
    "MC": {"src": r"G:\录像\MC", "work": r"G:\autocut_work\mc",
           "cuts": r"G:\autocut_work\mc\{base}.cuts.json",
           "voice": r"C:\Users\28568\Desktop\AI\WORK\autocut\_audio_final\{base}.wav",
           "clips": r"G:\autocut_work\mc\_clips_sep\{base}",
           "prefix": "MC", "name": "MC全库精华"},
    "RDR": {"src": r"G:\录像\dabiaoge", "work": r"G:\autocut_work\dabiaoge",
            "cuts": r"G:\autocut_work\dabiaoge\{base}.rdr.cuts.json",
            "voice": r"G:\autocut_work\dabiaoge\{base}.voice.wav",
            "clips": r"G:\autocut_work\dabiaoge\_clips_sep\{base}",
            "prefix": "RDR", "name": "RDR全库精华"},
}


def run(args, tail=3):
    r = subprocess.run([PY] + args, capture_output=True, text=True, errors="replace")
    for line in (r.stdout or "").strip().splitlines()[-tail:]:
        print("      ", line)
    if r.returncode != 0:
        for line in (r.stderr or "").strip().splitlines()[-4:]:
            print("      E", line)
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True, choices=["MC", "RDR"])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--only", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = CFG[a.batch]
    plan = json.load(open(a.plan, encoding="utf-8"))
    rows = [r for r in plan if r["batch"] == a.batch]
    if a.only:
        rows = [r for r in rows if a.only in r["file"]]

    scenes = []
    for r in rows:
        base = r["file"]
        src = os.path.join(cfg["src"], base + ".mkv")
        if not os.path.exists(src):
            src = os.path.join(cfg["src"], base + ".mp4")
        # 切点文件名有几种,且可能并存 —— 必须取**有内容**的那个。
        # 实测 01-31 同时存在 .rdr.cuts.json(0 段,组装失败的残留)
        # 与 .cuts.json(14 段,解说文稿选段),按"存在即取"会拿到空的那个。
        cut_cands = [cfg["cuts"].format(base=base),
                     cfg["cuts"].format(base=base).replace(".rdr.cuts.json", ".cuts.json")]
        cuts, n_cuts = None, 0
        for c in cut_cands:
            if not os.path.exists(c):
                continue
            try:
                n = len(json.load(open(c, encoding="utf-8"))["cuts"])
            except Exception:
                n = 0
            if n > n_cuts:
                cuts, n_cuts = c, n
        voice = cfg["voice"].format(base=base)
        if not os.path.exists(src) or not cuts or n_cuts == 0:
            continue
        n = n_cuts
        n = len(json.load(open(cuts, encoding="utf-8"))["cuts"])
        if n == 0:
            continue
        # 解说质量:GOOD/MARGINAL 才带解说轨
        use_voice = r.get("quality") in ("GOOD", "MARGINAL") and os.path.exists(voice)
        scenes.append({"base": base, "src": src, "cuts": cuts, "voice": voice,
                       "game_track": r["game_track"], "quality": r.get("quality"),
                       "n": n, "use_voice": use_voice,
                       "clips": cfg["clips"].format(base=base)})

    print(f"{'=' * 92}")
    print(f"最终重建 {a.batch}   场次 {len(scenes)}")
    print(f"{'=' * 92}")
    for s in scenes:
        print(f"  {s['base']:<26} {s['n']:>3} 段  游戏轨{s['game_track']}  "
              f"质量 {s['quality']}  {'→ 视频轨 + 独立解说轨' if s['use_voice'] else '→ 仅视频轨(游戏原声)'}")
    if a.dry_run:
        return 0

    # ---- 1) 分离模式导出 ----
    print(f"\n{'#' * 92}\n# 步骤 1:分离导出(视频=游戏原声,解说=独立 m4a)\n{'#' * 92}")
    for i, s in enumerate(scenes, 1):
        print(f"\n[{i}/{len(scenes)}] {s['base']}")
        os.makedirs(s["clips"], exist_ok=True)
        args = [os.path.join(HERE, "cut_clips.py"), "--cuts", s["cuts"],
                "--src", s["src"], "--track", str(s["game_track"]),
                "--dst", s["clips"]]
        if s["use_voice"]:
            args += ["--voice-wav", s["voice"], "--voice-separate"]
        run(args, tail=2)
        nv = len(glob.glob(os.path.join(s["clips"], "*.voice.m4a")))
        nm = len(glob.glob(os.path.join(s["clips"], "*.mp4")))
        print(f"      -> {nm} 视频 + {nv} 解说")

    # ---- 2) 建总草稿 ----
    print(f"\n{'#' * 92}\n# 步骤 2:建总草稿(含独立解说轨)\n{'#' * 92}")
    name = cfg["name"]
    folder = os.path.join(JY_ROOT, name)
    if os.path.exists(folder):
        shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(os.path.join(folder, "Timelines"), exist_ok=True)

    sys.path.insert(0, HERE)
    import make_draft as MD

    timelines = []
    total_v = total_a = 0
    t_now = int(time.time() * 1e6)
    for i, s in enumerate(scenes, 1):
        mp4s = sorted(glob.glob(os.path.join(s["clips"], "*.mp4")))
        if not mp4s:
            print(f"  !! {s['base']} 没有片段,跳过")
            continue
        voices = []
        for m in mp4s:
            vp = os.path.splitext(m)[0] + ".voice.m4a"
            voices.append(vp if os.path.exists(vp) else None)
        nv = sum(1 for v in voices if v)
        print(f"  [{i:>2}/{len(scenes)}] {s['base']:<26} {len(mp4s):>3} 视频 + {nv:>3} 解说")

        clips_info = [{"path": m, **MD.probe(m)} for m in mp4s]
        tmp_stage = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"),
                                 "autocut_sep")
        os.makedirs(tmp_stage, exist_ok=True)
        os.environ["AUTOCUT_ALLOW_REPLACE"] = "1"
        res = MD.build(f"__t_{i}", clips_info, 30.0, tmp_stage,
                       final_root=JY_ROOT,
                       clip_audios=voices if nv else None)
        src_dir = res["folder"]
        tid = str(uuid.uuid4()).upper()
        doc = res["doc"]
        doc["id"] = tid
        doc["name"] = name

        media_rel = f"_media/{tid}"
        dst_media = os.path.join(folder, media_rel)
        os.makedirs(dst_media, exist_ok=True)
        new_prefix = folder.replace("\\", "/") + "/" + media_rel + "/"
        # 收集所有素材实体(暂存目录里),逐个搬进总草稿
        stage_media = os.path.join(src_dir, "_media")
        want = set()
        for bucket in ("videos", "audios"):
            for mat in doc["materials"].get(bucket, []):
                want.add(os.path.basename(mat["path"].replace("/", os.sep)))
        moved = 0
        for fn in os.listdir(stage_media):
            if fn in want:
                shutil.copy2(os.path.join(stage_media, fn),
                             os.path.join(dst_media, fn))
                moved += 1
        for bucket in ("videos", "audios"):
            for mat in doc["materials"].get(bucket, []):
                mat["path"] = new_prefix + os.path.basename(mat["path"].replace("/", os.sep))
        # meta 登记表路径
        meta = res["meta"]
        for g in meta.get("draft_materials", []):
            if g.get("type") != 0:
                continue
            for rg in g.get("value") or []:
                rg["file_Path"] = new_prefix + os.path.basename(
                    (rg.get("file_Path") or "").replace("/", os.sep))
        meta["draft_name"] = name
        meta["draft_id"] = str(uuid.uuid4()).upper()
        meta["draft_fold_path"] = folder.replace("\\", "/")
        meta["draft_root_path"] = JY_ROOT.replace("\\", "/")
        meta["draft_json_file"] = folder.replace("\\", "/") + "/draft_info.json"

        tl_dir = os.path.join(folder, "Timelines", tid)
        os.makedirs(tl_dir, exist_ok=True)
        with open(os.path.join(tl_dir, "draft_info.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(doc, ensure_ascii=False))
        with open(os.path.join(tl_dir, "draft_meta_info.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False))
        if i == 1:
            main_doc, main_meta = doc, meta

        trk = " + ".join(f"{t['type']}({len(t['segments'])})" for t in doc["tracks"])
        print(f"        轨道: {trk}   搬入素材 {moved}")
        timelines.append({"create_time": t_now, "id": tid, "is_marked_delete": False,
                          "name": s["base"], "update_time": t_now})
        total_v += len(mp4s)
        total_a += nv
        shutil.rmtree(src_dir, ignore_errors=True)

    with open(os.path.join(folder, "draft_info.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(main_doc, ensure_ascii=False))
    with open(os.path.join(folder, "draft_meta_info.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(main_meta, ensure_ascii=False))
    project = {"config": {"color_space": -1, "hdr_vivid": False,
                          "mixed_track_mode_on": False,
                          "render_index_track_mode_on": True, "use_float_render": False},
               "create_time": t_now, "id": timelines[0]["id"],
               "main_timeline_id": timelines[0]["id"],
               "timelines": timelines, "update_time": t_now, "version": 0}
    with open(os.path.join(folder, "Timelines", "project.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(project, ensure_ascii=False))

    sz = sum(os.path.getsize(os.path.join(dp, f))
             for dp, dn, fn in os.walk(folder) for f in fn)
    print(f"\n{'=' * 92}")
    print(f"总草稿完成: {folder}")
    print(f"  {len(timelines)} 条时间线,{total_v} 视频片段 + {total_a} 解说片段")
    print(f"  占用 {sz / 1e9:.1f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
