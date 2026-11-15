r"""
build_masters.py —— 直接从片段目录建两个总草稿(不做单草稿)

⚠️ **批次专用脚本,不是通用工具。**
   下面的 BATCHES 配置写死了本批次的片段目录(`G:\autocut_work\...`)。
   换批次要改配置。通用管线见 SKILL.md「固定流程」。

为什么跳过单草稿:C 盘和 G 盘都很紧张。单草稿 + 总草稿会让素材存两份,
而用户要的成品本来就是"一个总草稿 + 每场一条时间线"。

空间策略:
  MC 总草稿 -> 建在 C 盘草稿库(片段 15.7 GB,复制进去;C 盘 51.7 GB 够)
  RDR 总草稿 -> 建在 G 盘(片段 29.4 GB,同卷硬链接,零额外占用)

用法:
  python build_masters.py --batch MC|RDR --plan _rebuild_plan.json
                          [--master-root C:\...\com.lveditor.draft | G:\...]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
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
    "MC": {"clips": r"G:\autocut_work\mc\_clips",
           "prefix": "MC", "name": "MC全库精华_v2"},
    "RDR": {"clips": r"G:\autocut_work\dabiaoge\_clips2",
            "prefix": "RDR", "name": "RDR全库精华_v2"},
}


def new_uid():
    return str(uuid.uuid4()).upper()


def link_or_copy(src, dst):
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True, choices=["MC", "RDR"])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--master-root", required=True,
                    help="总草稿建在哪(必须与片段同卷才能硬链接)")
    ap.add_argument("--only", default=None)
    a = ap.parse_args()

    cfg = CFG[a.batch]
    plan = json.load(open(a.plan, encoding="utf-8"))
    rows = [r for r in plan if r["batch"] == a.batch
            and r.get("quality") is not None or r["batch"] == a.batch]
    rows = [r for r in plan if r["batch"] == a.batch]
    if a.only:
        rows = [r for r in rows if a.only in r["file"]]

    # 收集每场的片段
    scenes = []
    for r in rows:
        base = r["file"]
        d = os.path.join(cfg["clips"], base)
        if not os.path.isdir(d):
            continue
        mp4s = sorted(glob.glob(os.path.join(d, "*.mp4")))
        if not mp4s:
            continue
        mixed = any("mixed" in os.path.basename(x) for x in mp4s)
        scenes.append({"base": base, "clips": mp4s,
                       "quality": r.get("quality"),
                       "game_track": r.get("game_track"),
                       "voice_track": r.get("voice_track"),
                       "mixed": (r.get("quality") in ("GOOD", "MARGINAL")
                                 and os.path.exists(
                                     os.path.join(r"G:\autocut_work\dabiaoge",
                                                  base + ".voice.wav"))
                                 if a.batch == "RDR" else
                                 (r.get("quality") in ("GOOD", "MARGINAL")))})

    if not scenes:
        print("没有可用的片段目录", file=sys.stderr)
        return 2

    name = cfg["name"]
    folder = os.path.join(a.master_root, name)
    if os.path.exists(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, "Timelines"), exist_ok=True)

    print(f"总草稿 {name}   建在 {a.master_root}")
    print(f"时间线 {len(scenes)} 条\n")

    # 复用 make_draft 的骨架:拿它的 build() 逐场生成到临时目录,再合并
    sys.path.insert(0, HERE)
    import make_draft as MD

    timelines = []
    total_clips = 0
    t_now = int(__import__("time").time() * 1e6)

    for i, sc in enumerate(scenes, 1):
        tid = new_uid()
        print(f"  [{i:>2}/{len(scenes)}] {sc['base']:<26} {len(sc['clips']):>3} 片段  "
              f"质量 {sc['quality']}  "
              f"{'混音' if sc['mixed'] else '原声'}")
        # 用 build() 生成到临时目录(final_root 指到最终位置以便路径正确)
        # ⚠️ 临时暂存**不能与片段同盘** —— build() 会把片段复制进暂存目录,
        #    若暂存也在 G 盘(片段所在盘),等于双份写入,G 盘只有 8 GB 会爆。
        #    放到系统盘的临时目录,容量通常更充裕。
        tmp_stage = os.environ.get("AUTOCUT_TMP_STAGE") or \
            os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "autocut_master")
        os.makedirs(tmp_stage, exist_ok=True)
        os.environ["AUTOCUT_ALLOW_REPLACE"] = "1"
        clips_info = [{"path": c, **MD.probe(c)} for c in sc["clips"]]
        res = MD.build(f"__t_{tid[:8]}", clips_info, 30.0, tmp_stage,
                       final_root=a.master_root)
        src_dir = res["folder"]
        # 把生成的 JSON 改造成该时间线的内容,并搬素材到总草稿的 _media/<tid>
        media_rel = f"_media/{tid}"
        dst_media = os.path.join(folder, media_rel)
        os.makedirs(dst_media, exist_ok=True)
        new_prefix = (folder.replace("\\", "/") + "/" + media_rel + "/")

        doc = res["doc"]
        doc["id"] = tid
        doc["name"] = name
        # ⚠️ 素材来源必须取【同卷的片段目录】,不能取暂存目录:
        #    os.link 跨卷会返回 EXDEV,静默回退成复制 —— 结果空间翻倍。
        #    实测因此在 G 盘爆满。
        #    make_draft 的 --final-root 让 JSON 路径指向最终位置,实体在暂存目录,
        #    所以这里显式用片段目录(clips_dir)作为来源。
        src_dir_by_name = {}
        for c in sc["clips"]:
            src_dir_by_name[os.path.basename(c)] = c
        copied = 0
        linked = 0
        for mat in doc["materials"]["videos"]:
            name_only = os.path.basename(mat["path"].replace("/", os.sep))
            cand = src_dir_by_name.get(name_only)
            if not cand or not os.path.exists(cand):
                # 回退到暂存目录
                cand = os.path.join(src_dir, "_media", name_only)
                if not os.path.exists(cand):
                    print(f"      !! 找不到素材实体: {name_only}")
                    continue
            how = link_or_copy(cand, os.path.join(dst_media, name_only))
            linked += (how == "link")
            copied += 1
            mat["path"] = new_prefix + name_only
        if copied != len(doc["materials"]["videos"]):
            print(f"      !! 只搬了 {copied}/{len(doc['materials']['videos'])} 个素材")
        if copied and linked != copied:
            print(f"      ⚠️ 只有 {linked}/{copied} 个是硬链接(其余被复制,会占空间)")
        tl_dir = os.path.join(folder, "Timelines", tid)
        os.makedirs(tl_dir, exist_ok=True)
        with open(os.path.join(tl_dir, "draft_info.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(doc, ensure_ascii=False))

        meta = res["meta"]
        meta["draft_name"] = name
        meta["draft_id"] = new_uid()
        meta["draft_fold_path"] = folder.replace("\\", "/")
        meta["draft_root_path"] = a.master_root.replace("\\", "/")
        meta["draft_json_file"] = folder.replace("\\", "/") + "/draft_info.json"
        # 登记表里的路径同步
        for g in meta.get("draft_materials", []):
            if g.get("type") != 0:
                continue
            for rg in g.get("value") or []:
                oldp = (rg.get("file_Path") or "").replace("/", os.sep)
                rg["file_Path"] = new_prefix + os.path.basename(oldp)
        for mat in doc["materials"]["videos"]:
            for g in meta.get("draft_materials", []):
                pass
        with open(os.path.join(tl_dir, "draft_meta_info.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False))
        if i == 1:
            main_doc, main_meta = doc, meta

        timelines.append({"create_time": t_now, "id": tid, "is_marked_delete": False,
                          "name": sc["base"], "update_time": t_now})
        total_clips += len(sc["clips"])
        shutil.rmtree(src_dir, ignore_errors=True)

    # 根文件 + project.json
    with open(os.path.join(folder, "draft_info.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(main_doc, ensure_ascii=False))
    with open(os.path.join(folder, "draft_meta_info.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(main_meta, ensure_ascii=False))
    project = {"config": {"color_space": -1, "hdr_vivid": False,
                          "mixed_track_mode_on": False,
                          "render_index_track_mode_on": True,
                          "use_float_render": False},
               "create_time": t_now, "id": timelines[0]["id"],
               "main_timeline_id": timelines[0]["id"],
               "timelines": timelines, "update_time": t_now, "version": 0}
    with open(os.path.join(folder, "Timelines", "project.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(project, ensure_ascii=False))

    print(f"\n总草稿已生成: {folder}")
    print(f"  {len(timelines)} 条时间线,{total_clips} 片段")
    print(f"  主时间线: {timelines[0]['name']}")
    sz = sum(os.path.getsize(os.path.join(dp, f))
             for dp, dn, fn in os.walk(folder) for f in fn)
    print(f"  占用 {sz / 1e9:.1f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
