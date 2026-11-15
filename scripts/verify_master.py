# -*- coding: utf-8 -*-
"""
verify_master.py —— 校验「总草稿」全部时间线的素材可达性与 ID 链

总草稿 = 一个草稿目录 + `Timelines/` 下多条时间线。`validate_draft.py`
只校验单条时间线(根文档),总草稿需要逐条时间线都过一遍 —— 这是本脚本的用途。

检查项(每条时间线):
  1. `materials.videos/audios[].path` 指向的文件是否真实存在
  2. 链1:`segment.material_id` 能否在 materials 里找到
  3. 链2:`materials.*[].local_material_id` 能否在
     `draft_meta_info.json` 的 `draft_materials[type=0]` 里找到
     (meta 被剪映加密时跳过,并明确标注)

用法:
    python verify_master.py <草稿目录>
    python verify_master.py <草稿目录> --quiet      # 只打印汇总

退出码:0 = 全部通过,1 = 有问题
"""
from __future__ import annotations

import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    folder = sys.argv[1].rstrip("\\/")
    quiet = "--quiet" in sys.argv

    if not os.path.isdir(folder):
        print(f"目录不存在: {folder}")
        return 2

    print(f"草稿: {folder}\n")

    # _media 概况
    media_files = media_bytes = 0
    for dp, _dn, fn in os.walk(folder, onerror=lambda e: None):
        if "_media" in dp.split(os.sep):
            for f in fn:
                media_files += 1
                try:
                    media_bytes += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
    print(f"_media: {media_files} 个文件, {media_bytes / 1e9:.2f} GB")

    tl_root = os.path.join(folder, "Timelines")
    if not os.path.isdir(tl_root):
        print("没有 Timelines/ —— 这是单时间线草稿,请用 validate_draft.py")
        return 2

    docs = []
    for d in sorted(os.listdir(tl_root)):
        p = os.path.join(tl_root, d, "draft_info.json")
        if os.path.exists(p):
            docs.append((d, p))
    print(f"时间线: {len(docs)} 条\n")

    bad_total = 0
    for tid, p in docs:
        doc = load(p)
        if doc is None:
            print(f"  [!! ] {tid[:8]}… 读不了 draft_info.json")
            bad_total += 1
            continue

        mats = doc.get("materials", {})
        vids = mats.get("videos", [])
        auds = mats.get("audios", [])
        allmats = {m["id"] for m in vids + auds if isinstance(m, dict) and "id" in m}

        # 1. 素材文件存在性
        missing = []
        for m in vids + auds:
            fp = (m.get("path") or "").replace("/", os.sep)
            if fp and not os.path.exists(fp):
                missing.append(os.path.basename(fp))

        # 2. 链1
        nseg = 0
        bad1 = 0
        for t in doc.get("tracks", []):
            for s in t.get("segments", []):
                nseg += 1
                if s.get("material_id") not in allmats:
                    bad1 += 1

        # 3. 链2
        bad2 = 0
        meta_state = "无"
        mp = os.path.join(tl_root, tid, "draft_meta_info.json")
        if os.path.exists(mp):
            meta = load(mp)
            if meta is None:
                meta_state = "已加密(跳过)"
            else:
                meta_state = "明文"
                regs = []
                for g in meta.get("draft_materials", []):
                    if isinstance(g, dict) and g.get("type") == 0:
                        regs = g.get("value") or []
                rid = {r["id"] for r in regs if isinstance(r, dict) and "id" in r}
                bad2 = sum(1 for m in vids + auds
                           if not m.get("local_material_id")
                           or m["local_material_id"] not in rid)

        n_audio_tracks = sum(1 for t in doc.get("tracks", [])
                             if t.get("type") == "audio")
        prob = len(missing) + bad1 + bad2
        bad_total += prob
        if not quiet or prob:
            print(f"  [{'OK ' if not prob else '!! '}] {tid[:8]}… "
                  f"素材{len(vids)}+{len(auds)} 段{nseg} 音轨{n_audio_tracks} "
                  f"| 缺失{len(missing)} 链1断{bad1} 链2断{bad2} meta:{meta_state}")
            for m in missing[:4]:
                print(f"          缺失: {m}")

    print(f"\n总问题数: {bad_total}")
    print("结论: " + ("全部通过 ✅" if not bad_total else "有问题 ❌"))
    return 1 if bad_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
