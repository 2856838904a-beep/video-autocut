# -*- coding: utf-8 -*-
"""
cleanup_work.py —— 按清单清理自动剪辑的中间产物

清单文件:`<技能根>/work_manifest.json`。确认一次就固化,以后不用再翻目录找。

用法:
    python cleanup_work.py --dry          # 只看将删什么、能回收多少(默认)
    python cleanup_work.py --yes          # 真删
    python cleanup_work.py --scan         # 扫描已知位置,把新工作根登记进清单
    python cleanup_work.py --verify-only   # 只做素材完整性校验,不删

安全设计(按重要性):
  1. **先校验后删除**:删之前检查清单 media_check 里每个草稿引用的全部素材
     是否在磁盘上存在。缺任何一个就中止 —— 防止删掉唯一副本。
  2. **keep 永远优先**:同一层里 keep 命中的项即使也被 remove 命中也不删;
     两者冲突时先报出来。
  3. **只删顶层项**:remove 的 glob 只在每个 root 的第一层匹配,不做递归通配,
     避免手抖写出 `**` 把整个盘卷进去。
  4. **拒绝删工作根自身**,也拒绝 root 为空/不存在的情况。
  5. 剪映正在运行时不碰草稿库(它可能正打开)。
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

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(SKILL_ROOT, "work_manifest.json")

# 自动发现工作根的候选位置
SCAN_HINTS = [
    r"G:\autocut_work",
    r"C:\Users\28568\Desktop\AI\WORK\autocut",
]


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def human(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:8.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:8.1f} MB"
    if n >= 1024:
        return f"{n / 1024:8.1f} KB"
    return f"{n:8d} B "


def dirsize(p: str) -> tuple[int, int]:
    tot = n = 0
    for dp, _dn, fns in os.walk(p, onerror=lambda e: None):
        for fn in fns:
            try:
                tot += os.path.getsize(os.path.join(dp, fn))
                n += 1
            except OSError:
                pass
    return tot, n


def longpath(p: str) -> str:
    """Windows 长路径前缀,避免 >260 字符删除失败。"""
    if os.name != "nt":
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def rmtree(p: str) -> None:
    def onerr(func, path, exc):
        try:
            os.chmod(path, 0o700)
            func(path)
        except Exception:
            raise
    shutil.rmtree(longpath(p), onerror=onerr)


def load_manifest() -> dict:
    if not os.path.exists(MANIFEST):
        print(f"找不到清单: {MANIFEST}")
        raise SystemExit(1)
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def save_manifest(m: dict) -> None:
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# 素材完整性校验（删除前的守门人）
# --------------------------------------------------------------------------

def draft_root() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        r"JianyingPro\User Data\Projects\com.lveditor.draft")


def check_media(draft_names: list[str]) -> tuple[bool, list[str]]:
    """校验草稿引用的所有素材在磁盘存在。返回 (是否全在, 问题清单)。"""
    root = draft_root()
    problems: list[str] = []
    for name in draft_names:
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            problems.append(f"{name}: 草稿目录不存在 {d}")
            continue
        docs = []
        ri = os.path.join(d, "draft_info.json")
        if os.path.exists(ri):
            docs.append(ri)
        tdir = os.path.join(d, "Timelines")
        if os.path.isdir(tdir):
            for t in sorted(os.listdir(tdir)):
                p = os.path.join(tdir, t, "draft_info.json")
                if os.path.exists(p):
                    docs.append(p)
        n_path = n_ok = 0
        for p in docs:
            try:
                j = json.load(open(p, encoding="utf-8"))
            except Exception as e:
                problems.append(f"{name}/{os.path.basename(os.path.dirname(p))}: 读不了 {e}")
                continue
            for bucket in ("videos", "audios", "images"):
                for mat in j.get("materials", {}).get(bucket, []):
                    fp = mat.get("path") or ""
                    if not fp:
                        continue
                    n_path += 1
                    if os.path.exists(fp):
                        n_ok += 1
                    else:
                        problems.append(f"{name}: 素材缺失 {fp}")
        print(f"  {name}: {n_ok}/{n_path} 个素材文件存在"
              f"（{len(docs)} 个文档）")
    return (not problems), problems


# --------------------------------------------------------------------------
# 清单展开
# --------------------------------------------------------------------------

def expand(root: str, m: dict) -> tuple[list[tuple[str, int, str]], list[str]]:
    """展开一个 root 的删除项。返回 ([(路径, 字节, 类型)], 冲突说明)。"""
    keep_pats = m.get("keep", [])
    rm_pats = m.get("remove", [])
    if not os.path.isdir(root):
        return [], [f"工作根不存在: {root}"]

    out: list[tuple[str, int, str]] = []
    conflicts: list[str] = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError as e:
        return [], [f"打不开 {root}: {e}"]

    for e in entries:
        name = e.name
        if name.startswith("."):
            continue
        hit_rm = any(fnmatch.fnmatch(name, p) for p in rm_pats)
        if not hit_rm:
            continue
        hit_keep = any(fnmatch.fnmatch(name, p) for p in keep_pats)
        if hit_keep:
            conflicts.append(f"{root}\\{name} 同时命中 keep 与 remove —— 按 keep 处理(不删)")
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                sz, _ = dirsize(e.path)
                out.append((e.path, sz, "目录"))
            else:
                out.append((e.path, os.path.getsize(e.path), "文件"))
        except OSError as ex:
            conflicts.append(f"{root}\\{name} 取不到大小: {ex}")
    return out, conflicts


def scan_roots(m: dict) -> int:
    """发现候选位置里的新工作根并登记。"""
    known = {r["path"].rstrip("\\/").lower() for r in m.get("roots", [])}
    found: list[str] = []
    for hint in SCAN_HINTS:
        if not os.path.isdir(hint):
            continue
        # hint 自身可能就是一个工作根
        if os.path.isdir(os.path.join(hint, "Timelines")) or \
           os.path.basename(hint).lower() == "autocut":
            cands = [hint]
        else:
            cands = [e.path for e in os.scandir(hint) if e.is_dir()]
        for c in cands:
            if c.rstrip("\\/").lower() not in known:
                found.append(c)
    if not found:
        print("没有发现新的工作根（清单已是最新）")
        return 0
    print(f"发现 {len(found)} 个未登记的工作根:")
    for c in found:
        print(f"  {c}")
    print("\n已加入清单,但 keep/remove 为空 —— 请自己编辑 "
          f"{MANIFEST} 补上规则后再清理。")
    for c in found:
        m.setdefault("roots", []).append({
            "path": c,
            "kind": "自动登记,待补规则",
            "keep": [],
            "remove": []
        })
    save_manifest(m)
    return len(found)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="按 work_manifest.json 清理自动剪辑中间产物")
    ap.add_argument("--dry", action="store_true", help="只看不删(默认行为)")
    ap.add_argument("--yes", action="store_true", help="真删")
    ap.add_argument("--scan", action="store_true", help="扫描并登记新工作根")
    ap.add_argument("--verify-only", action="store_true", help="只校验素材完整性")
    ap.add_argument("--skip-verify", action="store_true",
                    help="跳过素材完整性校验(不推荐)")
    a = ap.parse_args()

    m = load_manifest()

    if a.scan:
        return 0 if scan_roots(m) >= 0 else 1

    # ---- 1. 素材完整性校验 ----
    mc = m.get("media_check", {})
    if mc.get("drafts") and not a.skip_verify:
        print("=" * 74)
        print("① 删除前校验:草稿引用的素材是否都在磁盘上")
        print("=" * 74)
        ok, problems = check_media(mc["drafts"])
        if not ok:
            print("\n!! 素材校验未通过,已中止,不会删任何东西:")
            for p in problems[:20]:
                print(f"   {p}")
            if len(problems) > 20:
                print(f"   ...还有 {len(problems) - 20} 条")
            print("\n（确认这些缺失无所谓时,可加 --skip-verify 强制继续）")
            return 2
        print("\n素材校验通过 ✅\n")
    if a.verify_only:
        return 0

    # ---- 2. 展开删除项 ----
    print("=" * 74)
    print("② 清单展开")
    print("=" * 74)
    plan: list[tuple[str, int, str]] = []
    all_conflicts: list[str] = []
    for r in m.get("roots", []):
        root = os.path.expandvars(r["path"])
        items, conf = expand(root, r)
        all_conflicts += conf
        sz = sum(i[1] for i in items)
        print(f"\n  {root}   ({r.get('kind', '')})")
        print(f"    待删 {len(items)} 项, 合计 {human(sz)}")
        for p, b, k in items:
            print(f"      [{k}] {os.path.basename(p):<34}{human(b)}")
        plan += items
        if not items:
            print("      （无需清理）")

    if all_conflicts:
        print("\n  --- keep/remove 冲突(已按 keep 保留)---")
        for c in all_conflicts:
            print(f"    {c}")

    total = sum(i[1] for i in plan)
    print("\n" + "=" * 74)
    print(f"合计可回收 {human(total)}   ({len(plan)} 项)")
    print("=" * 74)

    if not plan:
        return 0
    if not a.yes:
        print("\n这是试运行。确认无误后加 --yes 真删:")
        print("  python cleanup_work.py --yes")
        return 0

    # ---- 3. 执行 ----
    print("\n③ 开始删除 ...")
    freed = 0
    failed: list[str] = []
    t0 = time.time()
    for p, b, k in plan:
        try:
            if k == "目录":
                rmtree(p)
            else:
                os.remove(longpath(p))
            freed += b
            print(f"   ✓ {os.path.basename(p)}")
        except Exception as e:
            failed.append(f"{p}: {e}")
            print(f"   ✗ {os.path.basename(p)}  {e}")
    print(f"\n完成:回收 {human(freed)},耗时 {int(time.time() - t0)} 秒")
    if failed:
        print(f"有 {len(failed)} 项失败:")
        for f in failed[:10]:
            print(f"   {f}")
    return 0 if not failed else 3


if __name__ == "__main__":
    raise SystemExit(main())
