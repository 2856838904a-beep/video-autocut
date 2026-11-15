# -*- coding: utf-8 -*-
"""
paths.py —— 集中解析技能运行所需的外部路径

**为什么要这个模块**

技能里的脚本原先各自硬编码了两种绝对路径:

  1. `C:\\Users\\28568\\...`(工作目录、素材目录)
     → 换个 Windows 账户就失效。技能装在 `~/.agents/skills` 是**用户级**的,
       意味着同机器上任何 agent / 任何工作区都会加载它 —— 但脚本里的
       绝对路径属于「上一次运行的那台机器」,会被一起带过去。

  2. `C:\\Users\\28568\\.dsh\\skills\\video-autocut\\scripts`
     → **这个目录早已不存在**(技能现已迁到 `~/.agents/skills`)。
       6 个批处理脚本因此直接跑不通:技能能被发现,但一跑就找不到子脚本。

结论:**能被发现 ≠ 能跑通**。所有路径解析统一走这里。

**用法**(脚本就在 scripts/ 下,`sys.path[0]` 即本目录,直接 import 即可):

    import paths
    FFMPEG = paths.ffmpeg()
    DRAFT_ROOT = paths.draft_root()
    SKILL = paths.SKILL_DIR          # 技能根目录
    SCRIPTS = paths.SCRIPTS_DIR

也支持命令行自检:`python paths.py`
"""
from __future__ import annotations

import glob
import os
import shutil
import sys

# --------------------------------------------------------------------------
# 技能自身位置（永远从 __file__ 推导,不写死）
# --------------------------------------------------------------------------
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
SKILL_ROOT = SKILL_DIR          # 别名,兼容两种叫法
REFERENCES_DIR = os.path.join(SKILL_DIR, "references")
MANIFEST = os.path.join(SKILL_DIR, "work_manifest.json")


def script(name: str) -> str:
    """技能内某个脚本的绝对路径。"""
    return os.path.join(SCRIPTS_DIR, name)


def reference(name: str) -> str:
    return os.path.join(REFERENCES_DIR, name)


# --------------------------------------------------------------------------
# 剪映草稿库
# --------------------------------------------------------------------------
def draft_root() -> str:
    """剪映专业版草稿库根目录。

    优先级:环境变量 JIANYING_DRAFT_ROOT > %LOCALAPPDATA% 默认位置 > ~/.dsh 猜测
    """
    env = os.environ.get("JIANYING_DRAFT_ROOT")
    if env and os.path.isdir(env):
        return env

    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    cand = os.path.join(local, "JianyingPro", "User Data", "Projects",
                        "com.lveditor.draft")
    if os.path.isdir(cand):
        return cand

    # 兜底:在常见位置里找一次
    for pat in (
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "JianyingPro",
                     "User Data", "Projects", "com.lveditor.draft"),
        os.path.join(local, "JianyingPro", "Projects", "com.lveditor.draft"),
    ):
        if os.path.isdir(pat):
            return pat
    return cand          # 返回默认值,由调用方处理不存在的情况


def capcut_draft_root() -> str:
    """CapCut(剪映海外版)草稿库,结构同理。"""
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "CapCut", "User Data", "Projects",
                        "com.lveditor.draft")


# --------------------------------------------------------------------------
# ffmpeg / ffprobe
# --------------------------------------------------------------------------
_FF_CACHE: dict[str, str | None] = {}

# 已知的安装位置模式（winget / scoop / choco / 手动解压）
_FF_PATTERNS = [
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\{exe}",
    r"%LOCALAPPDATA%\Microsoft\WinGet\Links\{exe}",
    r"%USERPROFILE%\scoop\shims\{exe}",
    r"%ProgramData%\chocolatey\bin\{exe}",
    r"C:\ffmpeg\bin\{exe}",
    r"C:\Program Files\ffmpeg\bin\{exe}",
    r"C:\Program Files (x86)\ffmpeg\bin\{exe}",
]


def _which(name: str) -> str | None:
    """在 PATH 里找,顺带处理 Windows 上 .exe 后缀。"""
    p = shutil.which(name) or shutil.which(name + ".exe")
    return p


def ffmpeg_tool(exe: str = "ffmpeg.exe") -> str:
    """找 ffmpeg / ffprobe。

    优先级:环境变量(FFMPEG / FFPROBE) > PATH > 已知安装位置。
    找不到时返回 exe 名字本身,让它在 PATH 上再试一次 —— 总比写死一个
    属于别人机器的绝对路径好。
    """
    if exe in _FF_CACHE:
        return _FF_CACHE[exe]  # type: ignore[return-value]

    envkey = "FFPROBE" if "probe" in exe else "FFMPEG"
    # 兼容脚本里沿用过的旧环境变量名
    env = (os.environ.get(envkey)
           or os.environ.get(f"AUTOCUT_{envkey}")
           or os.environ.get("FFMPEG_DIR"))
    if env:
        cand = env if env.lower().endswith(".exe") else os.path.join(env, exe)
        if os.path.isfile(cand):
            _FF_CACHE[exe] = cand
            return cand

    hit = _which(exe)
    if hit:
        _FF_CACHE[exe] = hit
        return hit

    for pat in _FF_PATTERNS:
        expanded = os.path.expandvars(pat.replace("{exe}", exe))
        for m in sorted(glob.glob(expanded)):
            if os.path.isfile(m):
                _FF_CACHE[exe] = m
                return m

    _FF_CACHE[exe] = exe
    return exe


def ffmpeg() -> str:
    return ffmpeg_tool("ffmpeg.exe")


def ffprobe() -> str:
    return ffmpeg_tool("ffprobe.exe")


# --------------------------------------------------------------------------
# 工作区 / 环境自检
# --------------------------------------------------------------------------
def workspace() -> str:
    """当前工作区根。优先 DSH 提供的值,否则用 cwd。"""
    for k in ("DSH_WORKSPACE", "WORKSPACE_ROOT"):
        v = os.environ.get(k)
        if v and os.path.isdir(v):
            return v
    return os.getcwd()


def fpenv() -> dict:
    """给 subprocess 用的环境(确保 PATH 里有 ffmpeg 所在目录)。"""
    e = dict(os.environ)
    d = os.path.dirname(ffmpeg())
    if d and os.path.isdir(d):
        e["PATH"] = d + os.pathsep + e.get("PATH", "")
    return e


def report() -> int:
    """自检:打印所有关键路径的解析结果。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ok = True
    print("=" * 74)
    print("video-autocut 路径自检")
    print("=" * 74)
    rows = [
        ("技能根 (SKILL_DIR)", SKILL_DIR),
        ("脚本目录 (SCRIPTS_DIR)", SCRIPTS_DIR),
        ("参考文档", REFERENCES_DIR),
        ("清理清单", MANIFEST),
        ("ffmpeg", ffmpeg()),
        ("ffprobe", ffprobe()),
        ("剪映草稿库", draft_root()),
        ("当前工作区", workspace()),
    ]
    for label, p in rows:
        ex = os.path.exists(p)
        if not ex:
            ok = False
        print(f"  {'✅' if ex else '❌'} {label:<24}{p}")

    # 关键脚本是否齐全
    print("\n  --- 关键脚本 ---")
    need = ["make_draft.py", "validate_draft.py", "cut_clips.py", "pick.py",
            "transcribe_long.py", "extract_audio.py", "analyze_tracks.py",
            "classify_voice.py", "fix_audio_track.py", "cleanup_work.py",
            "install_when_closed.py", "genres.py"]
    miss = []
    for n in need:
        p = script(n)
        if not os.path.isfile(p):
            miss.append(n)
    print(f"      {len(need) - len(miss)}/{len(need)} 就位"
          + (f"  缺: {', '.join(miss)}" if miss else ""))
    if miss:
        ok = False

    # 草稿库里有几个草稿
    dr = draft_root()
    if os.path.isdir(dr):
        n = sum(1 for d in os.listdir(dr)
                if os.path.isdir(os.path.join(dr, d)) and not d.startswith("."))
        print(f"      草稿库里有 {n} 个草稿")

    print("\n" + "=" * 74)
    print("结论:" + ("全部就绪 ✅" if ok else "有缺失 ❌ —— 见上面 ❌ 行"))
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(report())
