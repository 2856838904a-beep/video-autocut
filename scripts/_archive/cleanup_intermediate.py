r"""
cleanup_intermediate.py —— 【已弃用】清理中间草稿

⚠️ 请改用 cleanup_work.py。本脚本已被取代,保留仅为兼容旧命令。

弃用原因:
  1. SKILL 路径写死在旧的 `~\.dsh\skills\video-autocut`,该目录已不存在
     (技能现位于 `~\.agents\skills\video-autocut`)。
  2. 只管剪映草稿库,不管磁盘上的工作产物 —— 而磁盘产物才是体积大头
     (实测两个批次 258 GB,草稿只占其中一小部分)。
  3. 保留清单硬编码 `{"MC全库精华"}`,每换一批就要改代码,
     而不是像 cleanup_work.py 那样改清单数据。

现在统一用:
    python cleanup_work.py --dry      # 看将删什么
    python cleanup_work.py --yes      # 真删
    python cleanup_work.py --scan     # 登记新工作根

清理规则改 `work_manifest.json`,不要改代码。

--- 以下为原实现,逻辑仍然正确,仅作参考 ---

保留 MC全库精华(总草稿);删除它依赖的 17 个单草稿 + 3 个测试草稿。

★ 为什么删单草稿不会损坏总草稿:
   合并用的是硬链接(os.link),已用 fsutil hardlink list 验证:
   同一个 mp4 有 2 个链接路径(单草稿里一份、总草稿里一份),
   nlink=2。删掉单草稿只减少一个链接,总草稿那份数据仍在。

安全设计:
  - 剪映主程序运行时拒绝删除(它可能正打开这些草稿)
  - 带 .locked 的跳过
  - 全部移入 .recycle_bin,可用 remove_draft.py --restore 恢复
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 直接转发到新脚本,避免旧命令静默走到过时逻辑
_NEW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cleanup_work.py")
if os.path.exists(_NEW) and os.environ.get("AUTOCUT_LEGACY_CLEANUP") != "1":
    print("⚠️  cleanup_intermediate.py 已弃用,转发到 cleanup_work.py")
    print("    规则请改 work_manifest.json,不要改代码。")
    print("    真要跑旧逻辑:设 AUTOCUT_LEGACY_CLEANUP=1\n")
    raise SystemExit(subprocess.run(
        [sys.executable, _NEW] + sys.argv[1:]).returncode)

SKILL = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                    r"JianyingPro\User Data\Projects\com.lveditor.draft")
KEEP = {"MC全库精华"}


def procs(exe: str) -> bool:
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
                       capture_output=True, text=True, errors="replace")
    out = r.stdout or ""
    if "no tasks" in out.lower() or "信息:" in out:
        return False
    return any(exe.split(".")[0] in l for l in out.splitlines())


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=1800,
                    help="等剪映退出的最长秒数(默认 1800=30 分钟)")
    ap.add_argument("--no-wait", action="store_true",
                    help="不等待;若剪映在运行就直接退出(返回码 3)")
    a = ap.parse_args()

    targets = []
    for d in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, d)
        if not os.path.isdir(p) or d.startswith("."):
            continue
        if d in KEEP:
            continue
        if (d.startswith("MC_") or d.startswith("MC高光")
                or d.startswith("MC生存") or d.startswith("AI_TEST")
                or d.startswith("合并测试")):
            targets.append(d)

    if not targets:
        print("没有需要清理的草稿")
        return 0

    print(f"待清理 {len(targets)} 个中间草稿(保留 {', '.join(KEEP)}):")
    for d in targets:
        print(f"  {d}")

    if procs("JianyingPro.exe"):
        if a.no_wait:
            print("\n!! 剪映主程序在运行,已按 --no-wait 直接退出。")
            print("   请完全关闭剪映后重跑本脚本。")
            return 3
        print(f"\n剪映主程序在运行,等待它退出(最多 {a.timeout} 秒)...")
        print("  请在剪映里保存好手头的工程,然后【完全关闭】它")
        print("  (任务管理器里确认没有 JianyingPro.exe 残留)")
        t0 = time.time()
        last = -1
        while procs("JianyingPro.exe"):
            el = int(time.time() - t0)
            if el > a.timeout:
                print(f"\n超时({a.timeout}s)。请完全关闭剪映后手动跑:")
                print(f"  python {os.path.join(SKILL, 'remove_draft.py')} "
                      f"--pattern 'MC_*' --yes")
                return 2
            if el // 30 != last // 30:
                print(f"  [{el:>4}s] 仍在运行 ...")
                last = el
            time.sleep(3)
        print(f"剪映已退出(等待 {int(time.time() - t0)} 秒)")
        time.sleep(1.5)

    args = [sys.executable, os.path.join(SKILL, "remove_draft.py")]
    for d in targets:
        args += ["--name", d]
    args.append("--yes")
    r = subprocess.run(args, text=True, errors="replace")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
