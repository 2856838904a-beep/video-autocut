"""
merge_timelines.py —— 把多个单时间线草稿合并成「一个总草稿 + 多条时间线」

剪映的多时间线结构(实测,样本 = 剪映自己处理过的草稿):
    <草稿>/
      draft_info.json                        ← 主时间线,id == main_timeline_id
      _media/<timeline>/...                  ← 各时间线自己的素材
      Timelines/
        project.json                         ← 清单:main_timeline_id + timelines[]
        <timeline_id>/draft_info.json        ← 每条时间线自己的一份(内容同根那份)

结构要点:
  - project.json.timelines[] 每项:{id, name, create_time, update_time, is_marked_delete}
  - 每条时间线的目录里有一份 draft_info.json,与根目录那份同样是明文
  - 素材按草稿目录写绝对路径,所以各时间线用自己的 _media 子目录最省事

用法:
  python merge_timelines.py --name "MC全库精华" --draft <草稿A> --draft <草稿B> ...
                            --out-root <暂存目录> --final-root <剪映草稿库>
                            [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
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
SKILL = HERE


def new_uid() -> str:
    return str(uuid.uuid4()).upper()


def link_or_copy(src: str, dst: str) -> str:
    """
    优先硬链接,失败退回复制。

    为什么用硬链接:多个时间线的素材其实是同一批 mp4 的副本(逐字节相同,
    已用哈希验证)。硬链接不占额外空间、且写入任意一方不影响另一方
    (剪映生成代理/缓存是另建文件,不会原地改素材)。
    实测:总草稿 17 条时间线若全复制要 17 GB,C 盘当时只剩 0 GB。
    """
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def load_main(draft_dir: str) -> dict:
    for fn in ("draft_info.json", "draft_content.json"):
        p = os.path.join(draft_dir, fn)
        if os.path.exists(p) and open(p, "rb").read(1) == b"{":
            return json.load(open(p, encoding="utf-8"))
    raise SystemExit(f"找不到明文主草稿: {draft_dir}")


def summary(doc: dict) -> tuple[int, float]:
    segs = sum(len(t.get("segments", [])) for t in doc.get("tracks", []))
    return segs, (doc.get("duration") or 0) / 1e6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="总草稿名")
    ap.add_argument("--draft", action="append", default=[],
                    help="源草稿目录(每个变成一条时间线),可重复")
    ap.add_argument("--timeline-name", action="append", default=[],
                    help="与 --draft 一一对应的显示名;留空则用源草稿名")
    ap.add_argument("--out-root", required=True, help="生成到哪里(暂存区)")
    ap.add_argument("--final-root", default=None,
                    help="草稿最终位置;给了就按它写路径,方便之后整体搬走")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.draft:
        print("至少要一个 --draft", file=sys.stderr)
        return 2

    path_root = a.final_root or a.out_root
    folder = os.path.join(a.out_root, a.name)
    path_folder = os.path.join(path_root, a.name).replace("\\", "/")

    # ---- 读源草稿 ----
    srcs = []
    for i, d in enumerate(a.draft):
        if not os.path.isdir(d):
            print(f"  跳过(不存在): {d}")
            continue
        doc = load_main(d)
        segs, secs = summary(doc)
        name = a.timeline_name[i] if i < len(a.timeline_name) else os.path.basename(d)
        srcs.append({"dir": d, "doc": doc, "name": name, "segs": segs, "secs": secs})
    if not srcs:
        print("没有可用的源草稿", file=sys.stderr)
        return 2

    print(f"合并 {len(srcs)} 个源草稿 -> 一个总草稿「{a.name}」")
    print(f"{'序':>3}  {'时间线名':<24} {'片段':>5} {'时长':>9}")
    print("-" * 50)
    for i, s in enumerate(srcs, 1):
        print(f"{i:>3}  {s['name']:<24} {s['segs']:>5} {s['secs']:>8.1f}s")
    tot_segs = sum(s["segs"] for s in srcs)
    tot_secs = sum(s["secs"] for s in srcs)
    print("-" * 50)
    print(f"{'合计':>3}  {'':<24} {tot_segs:>5} {tot_secs:>8.1f}s "
          f"({tot_secs / 60:.1f} 分钟)")

    if a.dry_run:
        print("\n[dry-run] 不写盘")
        return 0

    # ---- 生成 ----
    if os.path.exists(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, "Timelines"), exist_ok=True)

    t_now = int(time.time() * 1e6)
    timelines = []
    written = []
    stats_how: dict[str, int] = {}

    for i, s in enumerate(srcs, 1):
        tid = new_uid()
        doc = copy.deepcopy(s["doc"])
        doc["id"] = tid
        doc["name"] = a.name

        # 素材:复制到该时间线自己的 _media/ 并重写路径
        media_rel = f"_media/{tid}"
        dst_media = os.path.join(folder, media_rel)
        os.makedirs(dst_media, exist_ok=True)
        copied: dict[str, str] = {}          # 绝对源路径 -> 新的草稿内绝对路径
        registrations: list[dict] = []       # 重建的登记表(不依赖源 meta)

        for mat in doc.get("materials", {}).get("videos", []):
            old = mat.get("path") or ""
            if not old:
                continue
            src_file = old.replace("/", os.sep)
            if not os.path.exists(src_file):
                print(f"    !! 素材缺失: {old}")
                continue
            key = os.path.abspath(src_file)
            if key not in copied:
                dst = os.path.join(dst_media, os.path.basename(src_file))
                k = 1
                stem, ext = os.path.splitext(os.path.basename(src_file))
                while os.path.exists(dst):
                    dst = os.path.join(dst_media, f"{stem}_{k}{ext}")
                    k += 1
                how = link_or_copy(src_file, dst)
                stats_how[how] = stats_how.get(how, 0) + 1
                copied[key] = f"{path_folder}/{media_rel}/{os.path.basename(dst)}"
            new_path = copied[key]
            # 重建登记条目:每个素材一条,id 与 local_material_id 一致
            reg_id = mat.get("local_material_id") or new_uid()
            mat["path"] = new_path
            mat["local_material_id"] = reg_id
            registrations.append({
                "ai_group_type": "", "create_time": -1,
                "duration": mat.get("duration") or 0,
                "enter_from": 0, "extra_info": mat.get("material_name") or "",
                "file_Path": new_path,
                "height": mat.get("height") or 1080,
                "id": reg_id,
                "import_time": -1, "import_time_ms": -1, "item_source": 1,
                "material_color_tag": "", "md5": "", "metetype": "video",
                "roughcut_time_range": {"duration": -1, "start": -1},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0, "width": mat.get("width") or 1920,
            })

        # draft_meta_info 的素材登记:从源草稿读,重写 file_Path
        src_meta_p = os.path.join(s["dir"], "draft_meta_info.json")
        reg_map = {}
        if os.path.exists(src_meta_p) and open(src_meta_p, "rb").read(1) == b"{":
            sm = json.load(open(src_meta_p, encoding="utf-8"))
            for g in sm.get("draft_materials", []):
                if g.get("type") != 0:
                    continue
                for r in g.get("value") or []:
                    fp = (r.get("file_Path") or "").replace("/", os.sep)
                    if os.path.exists(fp):
                        # 找同名的已复制文件
                        for key, newp in copied.items():
                            if os.path.basename(key) == os.path.basename(fp):
                                reg_map[r["id"]] = newp
        # 让每个素材的 local_material_id 指向它自己的登记 id(源草稿已保证一致)

        # 写这条时间线
        tl_dir = os.path.join(folder, "Timelines", tid)
        os.makedirs(tl_dir, exist_ok=True)
        body = json.dumps(doc, ensure_ascii=False)
        with open(os.path.join(tl_dir, "draft_info.json"), "w", encoding="utf-8") as f:
            f.write(body)

        # --- 该时间线的 draft_meta_info.json(用重建的登记表) ---
        root_meta = None
        src_meta_p = os.path.join(s["dir"], "draft_meta_info.json")
        if os.path.exists(src_meta_p) and open(src_meta_p, "rb").read(1) == b"{":
            try:
                root_meta = json.load(open(src_meta_p, encoding="utf-8"))
            except Exception:
                root_meta = None
        if root_meta is None:
            # 源 meta 缺失或已被剪映加密 —— 用最小骨架(字段取自实测模板)
            root_meta = {
                "cloud_package_completed_time": "",
                "draft_cloud_last_action_download": False,
                "draft_cloud_materials": [], "draft_cloud_purchase_info": "",
                "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
                "draft_cloud_videocut_purchase_info": "",
                "draft_cover": "", "draft_deeplink_url": "",
                "draft_enterprise_info": {
                    "draft_enterprise_extra": "", "draft_enterprise_id": "",
                    "draft_enterprise_name": "", "enterprise_material": []},
                "draft_is_ai_packaging_used": False, "draft_is_ai_shorts": False,
                "draft_is_ai_translate": False, "draft_is_article_video_draft": False,
                "draft_is_from_deeplink": "false", "draft_is_invisible": False,
                "draft_materials_copied_info": [], "draft_new_version": "",
                "draft_removable_storage_device": "", "draft_segment_extra_info": [],
                "draft_type": "", "tm_draft_cloud_completed": "",
                "tm_draft_cloud_modified": 0, "tm_draft_removed": 0,
            }
        # 统一覆盖身份与登记表(登记表用重建的,保证 ID 链一致)
        root_meta["draft_materials"] = (
            [{"type": 0, "value": registrations}]
            + [{"type": t, "value": []} for t in (1, 2, 3, 6, 7, 8)]
        )
        root_meta["draft_name"] = a.name
        root_meta["draft_id"] = new_uid()
        root_meta["draft_fold_path"] = path_folder
        root_meta["draft_root_path"] = (
            a.final_root.replace("\\", "/") if a.final_root
            else a.out_root.replace("\\", "/"))
        root_meta["draft_json_file"] = f"{path_folder}/draft_info.json"
        root_meta["tm_draft_create"] = t_now
        root_meta["tm_draft_modified"] = t_now
        root_meta["tm_duration"] = doc.get("duration") or 0
        tl_dir = os.path.join(folder, "Timelines", tid)
        with open(os.path.join(tl_dir, "draft_meta_info.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(root_meta, ensure_ascii=False))
        if i == 1:
            main_meta = copy.deepcopy(root_meta)

        timelines.append({"create_time": t_now, "id": tid,
                          "is_marked_delete": False, "name": s["name"],
                          "update_time": t_now})
        written.append((s["name"], tid, s["segs"], s["secs"]))
        print(f"  时间线 {i}: {s['name']:<24} id={tid[:8]}…  "
              f"{s['segs']} 段 {s['secs']:.1f}s  素材 {len(copied)} 个")

    # ---- 根 draft_info.json(内容 = 主时间线那份)----
    root_doc = load_main(os.path.join(folder, "Timelines", timelines[0]["id"]))
    with open(os.path.join(folder, "draft_info.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(root_doc, ensure_ascii=False))

    # ---- 根 draft_meta_info.json(内容同主时间线那份,路径指向根) ----
    main_meta["draft_json_file"] = f"{path_folder}/draft_info.json"
    with open(os.path.join(folder, "draft_meta_info.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(main_meta, ensure_ascii=False))

    # ---- Timelines/project.json ----
    project = {
        "config": {"color_space": -1, "hdr_vivid": False, "mixed_track_mode_on": False,
                   "render_index_track_mode_on": True, "use_float_render": False},
        "create_time": t_now,
        "id": timelines[0]["id"],
        "main_timeline_id": timelines[0]["id"],
        "timelines": timelines,
        "update_time": t_now,
        "version": 0,
    }
    with open(os.path.join(folder, "Timelines", "project.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(project, ensure_ascii=False))

    print(f"\n总草稿已生成: {folder}")
    print(f"时间线 {len(timelines)} 条,片段共 {tot_segs} 个,总长 {tot_secs / 60:.1f} 分钟")
    print(f"素材处理: 硬链接 {stats_how.get('link', 0)} 个 / 复制 {stats_how.get('copy', 0)} 个")
    print(f"主时间线: {timelines[0]['name']} ({timelines[0]['id'][:8]}…)")
    print(f"路径按 {'final-root' if a.final_root else 'out-root'} 写: {path_folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
