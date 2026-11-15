# -*- coding: utf-8 -*-
"""
patch_paths.py —— 一次性批量修复技能脚本里的失效/易失效硬编码路径

修两类:
  A. `SKILL = r"C:\\Users\\28568\\.dsh\\skills\\video-autocut\\scripts"`
     —— 该目录**已不存在**(技能现装于 ~\\.agents\\skills),脚本一跑就找不到
     子脚本。改成从 __file__ 推导。
  B. `FFMPEG = (r"C:\\Users\\...\\WinGet\\Packages" ... )`
     —— 写死某个用户的 winget 安装路径。改成走 paths.ffmpeg()。

用法:
    python patch_paths.py --dry     # 只看改什么
    python patch_paths.py           # 真改(自动 .bak 备份)
"""
from __future__ import annotations

import os
import re
import shutil
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

DEAD_SKILL = re.compile(
    r'^SKILL\s*=\s*r?["\']C:\\Users\\[^"\']*\\\.dsh\\skills\\[^"\']*["\']\s*$',
    re.M)

FFMPEG_BLOCK = re.compile(
    r'FFMPEG\s*=\s*\(\s*r?["\'][^"\']*WinGet[^"\']*["\']'
    r'(?:\s*r?["\'][^"\']*["\'])*\s*\)',
    re.S)

FFPROBE_DERIVED = re.compile(
    r'^FFPROBE\s*=\s*FFMPEG\.replace\([^)]*\)\s*$', re.M)

HAS_IMPORT_PATHS = re.compile(r'^\s*import\s+paths\s*$|^\s*from\s+paths\s+import',
                              re.M)
HAS_IMPORT_OS = re.compile(r'^\s*import\s+os\s*$|^\s*import\s+os\s*,', re.M)


def find_ffmpeg_block(lines: list[str]) -> tuple[int, int] | None:
    """定位 `FFMPEG = ...` 整条赋值语句（可能跨多行、可能被 env.get 包裹）。

    返回 (起始行号, 结束行号) 含首尾；不是写死 WinGet 路径就返回 None。
    用括号配平来定边界,比正则可靠。
    """
    for i, ln in enumerate(lines):
        if not re.match(r'\s*FFMPEG\s*=', ln):
            continue
        # 从该行起收集到括号配平且语句结束
        depth = 0
        j = i
        while j < len(lines):
            depth += lines[j].count("(") - lines[j].count(")")
            ends = lines[j].rstrip().endswith((")", ",", '"', "'"))
            if depth <= 0 and ends and j >= i:
                break
            j += 1
        j = min(j, len(lines) - 1)
        block = "\n".join(lines[i:j + 1])
        if "WinGet" in block or "AppData\\Local\\Microsoft" in block:
            return (i, j)
        return None          # 已经走 paths 或没写死,不动
    return None


def patch(path: str, dry: bool) -> list[str]:
    src = open(path, encoding="utf-8").read()
    orig = src
    notes: list[str] = []

    # ---- A. 失效的 SKILL 路径 ----
    if DEAD_SKILL.search(src):
        src = DEAD_SKILL.sub(
            "SKILL = os.path.dirname(os.path.abspath(__file__))", src)
        notes.append("SKILL: 失效的 ~\\.dsh\\skills → 从 __file__ 推导")
        if not HAS_IMPORT_OS.search(src):
            # 在第一个 import 之前插一行
            m = re.search(r'^(import |from )', src, re.M)
            if m:
                src = src[:m.start()] + "import os\n" + src[m.start():]
                notes.append("补 import os")

    # ---- B. 写死的 ffmpeg 路径 ----
    lines = src.split("\n")
    span = find_ffmpeg_block(lines)
    if span:
        i, j = span
        old = "\n".join(lines[i:j + 1])
        lines[i:j + 1] = ["FFMPEG = paths.ffmpeg()"]
        src = "\n".join(lines)
        notes.append(f"FFMPEG: 写死的 WinGet 路径({j - i + 1} 行) → paths.ffmpeg()")
        if FFPROBE_DERIVED.search(src):
            src = FFPROBE_DERIVED.sub("FFPROBE = paths.ffprobe()", src)
            notes.append("FFPROBE: → paths.ffprobe()")
        if not HAS_IMPORT_PATHS.search(src):
            # 必须插在 FFMPEG 赋值之前 —— 那里一定在所有 import 之后。
            # 早先版本插在文件第一个 import 之前,结果跑到
            # `from __future__ import annotations` 前面,直接 SyntaxError。
            src = src.replace("FFMPEG = paths.ffmpeg()",
                              "import paths\n\nFFMPEG = paths.ffmpeg()", 1)
            notes.append("补 import paths(插在 FFMPEG 赋值前)")

    if not notes:
        return []
    if src == orig:
        return []

    if dry:
        return notes

    shutil.copy2(path, path + ".bak")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    return notes


def restore() -> int:
    """从 .bak 回滚所有被本补丁改过的文件。"""
    n = 0
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith(".py.bak"):
            continue
        bak = os.path.join(HERE, fn)
        dst = bak[:-4]
        shutil.copy2(bak, dst)
        os.remove(bak)
        print(f"  已回滚 {os.path.basename(dst)}")
        n += 1
    print(f"\n回滚 {n} 个文件")
    return 0


def main() -> int:
    if "--restore" in sys.argv:
        return restore()
    dry = "--dry" in sys.argv
    files = sorted(f for f in os.listdir(HERE) if f.endswith(".py"))
    total = 0
    for fn in files:
        if fn in ("patch_paths.py", "paths.py"):
            continue
        p = os.path.join(HERE, fn)
        try:
            notes = patch(p, dry)
        except Exception as e:
            print(f"  !! {fn}: {e}")
            continue
        if notes:
            total += 1
            print(f"\n{'[试运行] ' if dry else ''}{fn}")
            for n in notes:
                print(f"    - {n}")
    print(f"\n{'将修改' if dry else '已修改'} {total} 个文件")
    if dry:
        print("确认无误后去掉 --dry 真改（会自动写 .bak 备份）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
