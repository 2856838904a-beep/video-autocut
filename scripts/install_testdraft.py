"""
把暂存草稿安装进剪映草稿库。

纯复制,不做任何路径替换 —— 路径由 make_draft.py --final-root 一次写对。
(之前用字符串替换踩过坑:--media-src 的值恰好是目标路径的前缀,替换链式污染。)
"""
import json
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

if len(sys.argv) > 1:
    STAGE = sys.argv[1]
else:
    print("用法: python install_testdraft.py <草稿目录>")
    raise SystemExit(2)
ROOT = paths.draft_root()
NAME = os.path.basename(os.path.abspath(STAGE).rstrip("\\/"))
DEST = os.path.join(ROOT, NAME)


def procs(exe: str) -> list[str]:
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
                       capture_output=True, text=True, errors="replace")
    out = r.stdout or ""
    if "no tasks" in out.lower() or "信息:" in out:
        return []
    return [l for l in out.splitlines() if exe.split(".")[0] in l]


main_p = procs("JianyingPro.exe")
tray_p = procs("JianyingProTray.exe")
print(f"主程序 JianyingPro.exe : {'运行中' if main_p else '未运行'}")
print(f"托盘 JianyingProTray.exe: {'运行中' if tray_p else '未运行'}")
if main_p:
    print("\n!! 剪映主程序在运行,会覆盖草稿。请完全退出后重跑。")
    sys.exit(3)

if os.path.exists(DEST):
    print(f"移除旧目录: {DEST}")
    shutil.rmtree(DEST)
shutil.copytree(STAGE, DEST)

print(f"\n已放入草稿库: {DEST}")
for f in sorted(os.listdir(DEST)):
    fp = os.path.join(DEST, f)
    print(f"  {f:<24} {'<dir>' if os.path.isdir(fp) else str(os.path.getsize(fp)) + ' bytes'}")
media = os.path.join(DEST, "_media")
if os.path.isdir(media):
    for f in sorted(os.listdir(media)):
        print(f"    _media/{f}  {os.path.getsize(os.path.join(media, f))} bytes")

# --- 回读确认:路径必须都指向草稿库内 ---
doc = json.load(open(os.path.join(DEST, "draft_info.json"), encoding="utf-8"))
meta = json.load(open(os.path.join(DEST, "draft_meta_info.json"), encoding="utf-8"))
print(f"\n回读 version={doc['version']} new_version={doc['new_version']!r} "
      f"片段={len(doc['tracks'][0]['segments'])} duration={doc['duration']}")
print("路径可达性(应全部 True):")
bad = 0
for m in doc["materials"]["videos"]:
    ok = os.path.exists(m["path"])
    bad += not ok
    print(f"   material  {ok}  {m['path']}")
for g in meta.get("draft_materials", []):
    if g.get("type") == 0:
        for r in g.get("value") or []:
            ok = os.path.exists(r["file_Path"])
            bad += not ok
            print(f"   registry  {ok}  {r['file_Path']}")
for k in ("draft_fold_path", "draft_root_path", "draft_json_file"):
    print(f"   {k} = {meta.get(k)}")
print("draft_fold_path 正确:", meta.get("draft_fold_path", "").endswith(NAME))
print("不可达路径数:", bad)
sys.exit(1 if bad or not meta.get("draft_fold_path", "").endswith(NAME) else 0)
