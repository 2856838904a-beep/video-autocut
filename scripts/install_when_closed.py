"""
install_when_closed.py —— 等剪映主程序退出后自动安装草稿

为什么要有这个:剪映运行时写入会被它的自动保存覆盖,所以必须先退出。
但让人"关掉再回来跑命令"很别扭 —— 这个脚本负责等,关了立刻装。

用法:
  python install_when_closed.py <暂存草稿目录> [--timeout 1800]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main_alive() -> bool:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/NH"],
                       capture_output=True, text=True, errors="replace")
    out = r.stdout or ""
    if "no tasks" in out.lower() or "信息:" in out:
        return False
    return any("JianyingPro.exe" in l for l in out.splitlines())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--timeout", type=int, default=1800, help="最多等多少秒")
    ap.add_argument("--poll", type=float, default=3.0)
    a = ap.parse_args()

    print(f"等待剪映主程序退出(最多 {a.timeout} 秒)...")
    print("  请在剪映里保存好手头的工程,然后完全关闭它。")

    t0 = time.time()
    while main_alive():
        if time.time() - t0 > a.timeout:
            print("\n超时:剪映仍在运行,未安装。稍后手动跑:")
            print(f"  python install_testdraft.py \"{a.stage}\"")
            return 2
        el = int(time.time() - t0)
        print(f"  [{el:>4}s] 剪映仍在运行 ...", flush=True)
        time.sleep(a.poll)

    print(f"\n剪映已退出(等待 {int(time.time() - t0)} 秒),开始安装...\n")
    time.sleep(1.5)          # 给它一点时间释放文件句柄
    r = subprocess.run([sys.executable, "install_testdraft.py", a.stage],
                       text=True, errors="replace")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
