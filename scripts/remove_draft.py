"""
remove_draft.py —— 安全删除剪映草稿(可恢复)

两个安全设计:
  1. 剪映自己有 .recycle_bin 机制,删到那里能靠剪映的回收站恢复
  2. 同时写一份清单 JSON 到 .recycle_bin,记录原名/路径/大小,便于人工还原

拒绝在剪映主程序运行时删除(它可能正在写)。

用法:
  python remove_draft.py --list                     # 列出所有草稿
  python remove_draft.py --name "MC高光_0708"       # 删除一个
  python remove_draft.py --name A --name B          # 删除多个
  python remove_draft.py --pattern "MC*" --yes      # 按通配符删
  python remove_draft.py --restore "MC高光_0708"    # 从回收站恢复
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

DRAFT_ROOT = paths.draft_root()
RECYCLE = os.path.join(DRAFT_ROOT, ".recycle_bin")
MANIFEST = os.path.join(RECYCLE, "_removed_by_autocut.json")


def procs(exe: str) -> list[str]:
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
                       capture_output=True, text=True, errors="replace")
    out = r.stdout or ""
    if "no tasks" in out.lower() or "信息:" in out:
        return []
    return [l for l in out.splitlines() if exe.split(".")[0] in l]


def dir_size(p: str) -> tuple[int, int]:
    tot, cnt = 0, 0
    for dirpath, _, names in os.walk(p):
        for nm in names:
            try:
                tot += os.path.getsize(os.path.join(dirpath, nm))
                cnt += 1
            except OSError:
                pass
    return tot, cnt


def load_manifest() -> list[dict]:
    if os.path.exists(MANIFEST):
        try:
            return json.load(open(MANIFEST, encoding="utf-8"))
        except Exception:
            return []
    return []


def save_manifest(items: list[dict]) -> None:
    os.makedirs(RECYCLE, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def cmd_list() -> int:
    dirs = sorted(d for d in os.listdir(DRAFT_ROOT)
                  if os.path.isdir(os.path.join(DRAFT_ROOT, d))
                  and not d.startswith("."))
    if not dirs:
        print("草稿库为空")
        return 0
    print(f"{'草稿名':<32} {'大小':>9} {'文件':>6}  修改时间")
    print("-" * 72)
    for d in dirs:
        p = os.path.join(DRAFT_ROOT, d)
        sz, cnt = dir_size(p)
        mt = time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p)))
        flag = "  [.locked]" if os.path.exists(os.path.join(p, ".locked")) else ""
        print(f"{d:<32} {sz / 1e6:>7.1f}MB {cnt:>6}  {mt}{flag}")
    print(f"\n共 {len(dirs)} 个草稿")
    return 0


def confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes", "是", "确认")
    except EOFError:
        return False


def cmd_remove(names: list[str], assume_yes: bool) -> int:
    if procs("JianyingPro.exe"):
        print("!! 剪映主程序在运行,拒绝删除(它可能正在写这些草稿)。")
        print("   请完全退出剪映后重试。")
        return 3

    targets = []
    for nm in names:
        p = os.path.join(DRAFT_ROOT, nm)
        if not os.path.isdir(p):
            print(f"  跳过(不存在): {nm}")
            continue
        if os.path.exists(os.path.join(p, ".locked")):
            print(f"  跳过(有 .locked,正被占用): {nm}")
            continue
        targets.append((nm, p))

    if not targets:
        print("没有可删除的目标")
        return 1

    print(f"\n将删除 {len(targets)} 个草稿(移到 .recycle_bin,可恢复):")
    total = 0
    for nm, p in targets:
        sz, cnt = dir_size(p)
        total += sz
        print(f"  {nm:<32} {sz / 1e6:>7.1f} MB  {cnt} 个文件")
    print(f"  合计 {total / 1e6:.1f} MB")

    if not assume_yes and not confirm("\n确认删除?[y/N] "):
        print("已取消")
        return 0

    os.makedirs(RECYCLE, exist_ok=True)
    manifest = load_manifest()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    moved = []
    for nm, p in targets:
        dst = os.path.join(RECYCLE, f"{nm}__{stamp}")
        try:
            shutil.move(p, dst)
            sz, cnt = dir_size(dst)
            manifest.append({"removed_at": stamp, "name": nm,
                             "recycle_path": dst, "bytes": sz, "files": cnt})
            moved.append(nm)
            print(f"  已移除 {nm}  ->  {os.path.basename(dst)}")
        except Exception as e:
            print(f"  失败 {nm}: {type(e).__name__}: {e}")

    save_manifest(manifest)
    print(f"\n完成:删除 {len(moved)} 个,共 {total / 1e6:.1f} MB")
    print(f"回收站: {RECYCLE}")
    print(f"清单:   {MANIFEST}")
    print("\n恢复方式:")
    print(f'  python remove_draft.py --restore "{moved[0]}"' if moved else "")
    return 0


def cmd_restore(name: str) -> int:
    manifest = load_manifest()
    hit = [m for m in manifest if m["name"] == name and os.path.isdir(m["recycle_path"])]
    if not hit:
        print(f"回收站里没有 {name!r}")
        av = [m["name"] for m in manifest if os.path.isdir(m["recycle_path"])]
        if av:
            print("可恢复的:", ", ".join(av))
        return 1
    m = hit[-1]
    dst = os.path.join(DRAFT_ROOT, name)
    if os.path.exists(dst):
        print(f"目标已存在,拒绝覆盖: {dst}")
        return 1
    shutil.move(m["recycle_path"], dst)
    manifest.remove(m)
    save_manifest(manifest)
    print(f"已恢复 {name}  ->  {dst}")
    print("提示:重启剪映(或进出一次旧草稿)刷新列表")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--name", action="append", default=[])
    ap.add_argument("--pattern", default=None, help="通配符,如 'MC*'")
    ap.add_argument("--yes", action="store_true", help="跳过确认")
    ap.add_argument("--restore", default=None)
    a = ap.parse_args()

    if a.list:
        return cmd_list()
    if a.restore:
        return cmd_restore(a.restore)

    names = list(a.name)
    if a.pattern:
        names += [d for d in os.listdir(DRAFT_ROOT)
                  if os.path.isdir(os.path.join(DRAFT_ROOT, d))
                  and not d.startswith(".")
                  and fnmatch.fnmatch(d, a.pattern)]
        names = list(dict.fromkeys(names))     # 去重保序

    if not names:
        print("没有指定要删除的草稿。用 --list 查看,或 --name/--pattern 指定。")
        return 2
    return cmd_remove(names, a.yes)


if __name__ == "__main__":
    raise SystemExit(main())
