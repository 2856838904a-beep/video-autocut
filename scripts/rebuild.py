r"""
rebuild.py —— 按"音轨结构分析 + 解说质量判定"重建两批成片

⚠️ **批次专用脚本,不是通用工具。**
   下面 CH 之类的配置段写死了本批次的 `G:\录像\MC` / `G:\录像\dabiaoge`
   路径与素材文件名。用在别的素材上必须先改配置段。
   通用管线请走 SKILL.md 的「固定流程」。
   外部路径已统一走 paths.py;本文件里剩下的绝对路径是批次配置,不是环境依赖。

这是对用户反馈的最终落实:
  「多音轨的先判断有效的音轨是哪些,有没有重复的不必要的,
    然后再分出游戏原声和游玩解说」

流程(每个文件):
  ① 读 analyze_tracks.py 的结构结论  -> 游戏原声取哪条、哪些是重复/静音
  ② 读 classify_voice.py 的质量结论  -> 解说能不能用
  ③ 按质量分流:
       GOOD      混音(游戏原声 + 提升后的解说)
       MARGINAL  混音但解说压低(存在感弱化,避免嘶声抢戏)
       UNUSABLE  只保留游戏原声
  ④ 导出片段 -> 生成草稿

用法:
  python rebuild.py --batch MC|RDR --plan <重建计划.json>
                    [--only <文件子串>] [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
ROOT = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                    r"JianyingPro\User Data\Projects\com.lveditor.draft")

# 每批的路径配置
CFG = {
    "RDR": {
        "src": r"G:\录像\dabiaoge",
        "work": r"G:\autocut_work\dabiaoge",
        "cuts": "{work}\\{base}.rdr.cuts.json",
        "voice": "{work}\\{base}.voice.wav",
        "clips": "{work}\\_clips2\\{base}",
        "stage": "{work}\\_stage2",
        "prefix": "RDR",
    },
    "MC": {
        "src": r"G:\录像\MC",
        "work": r"G:\autocut_work\mc",
        "cuts": "{work}\\{base}.cuts.json",
        "voice": "{src_audio}\\{base}.wav",
        "src_audio": r"C:\Users\28568\Desktop\AI\WORK\autocut\_audio_final",
        "clips": "{work}\\_clips\\{base}",
        "stage": "{work}\\_stage",
        "prefix": "MC",
    },
}


def sh(args, label="", tail=6):
    r = subprocess.run([PY] + args, capture_output=True, text=True, errors="replace")
    out = (r.stdout or "").strip().splitlines()
    for line in out[-tail:]:
        print("     ", line)
    if r.returncode != 0:
        print(f"     !! {label} 退出码 {r.returncode}")
        for line in (r.stderr or "").strip().splitlines()[-4:]:
            print("     E", line)
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True, choices=["MC", "RDR"])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--only", default=None, help="只处理名字含此子串的文件")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = CFG[a.batch]
    plan = json.load(open(a.plan, encoding="utf-8"))
    rows = [r for r in plan if r["batch"] == a.batch]
    if a.only:
        rows = [r for r in rows if a.only in r["file"]]
    if a.limit:
        rows = rows[: a.limit]

    print(f"{'=' * 96}")
    print(f"重建 {a.batch}   共 {len(rows)} 场")
    print(f"{'=' * 96}")

    os.environ["AUTOCUT_ALLOW_REPLACE"] = "1"
    results = []
    for i, r in enumerate(rows, 1):
        base = r["file"]
        src = os.path.join(cfg["src"], base + ".mkv")
        if not os.path.exists(src):
            # 兼容 .mp4
            alt = os.path.join(cfg["src"], base + ".mp4")
            if os.path.exists(alt):
                src = alt
            else:
                print(f"[{i}/{len(rows)}] {base} 源文件不存在,跳过")
                continue

        cuts = cfg["cuts"].format(work=cfg["work"], base=base)
        if cfg.get("src_audio"):
            voice = cfg["voice"].format(src_audio=cfg["src_audio"], base=base, work=cfg["work"])
        else:
            voice = cfg["voice"].format(work=cfg["work"], base=base)
        clips = cfg["clips"].format(work=cfg["work"], base=base)

        if not os.path.exists(cuts):
            print(f"[{i}/{len(rows)}] {base} 无切点({os.path.basename(cuts)}),跳过")
            results.append({**r, "skipped": "no-cuts"})
            continue

        n_cuts = len(json.load(open(cuts, encoding="utf-8"))["cuts"])
        if n_cuts == 0:
            print(f"[{i}/{len(rows)}] {base} 切点为 0,跳过")
            results.append({**r, "skipped": "empty"})
            continue

        g = r["game_track"]
        q = r["quality"]
        use_voice = (q in ("GOOD", "MARGINAL")) and os.path.exists(voice)
        print(f"\n{'#' * 96}")
        print(f"### [{i}/{len(rows)}] {base}   {n_cuts} 段  游戏轨{g}  "
              f"解说轨{r['voice_track']}  质量 {q}  → "
              f"{'混音' if use_voice else '只保留游戏原声'}")
        print(f"{'#' * 96}")

        if a.dry_run:
            continue

        # 导出片段
        os.makedirs(clips, exist_ok=True)
        args = [os.path.join(HERE, "cut_clips.py"), "--cuts", cuts, "--src", src,
                "--track", str(g), "--dst", clips]
        if use_voice:
            args += ["--voice-wav", voice]
            if q == "MARGINAL":
                # 压低:目标中位降到 -24 dBFS(默认 -14),存在感弱化
                args += ["--voice-gain", "0"]
        sh(args, "导出片段", tail=4)

        mp4s = sorted(glob.glob(os.path.join(clips, "*.mp4")))
        if not mp4s:
            print("     没有产出片段")
            results.append({**r, "skipped": "no-clips"})
            continue

        name = f"{cfg['prefix']}_{base.replace('2026-', '').replace('_', '-')}"
        # ⚠️ cfg["stage"] 是含 {work} 占位符的模板,必须 format 后再用。
        #    漏了这步会生成一个字面量名为 "{work}" 的目录(实测在 C 盘堆了 35 GB 垃圾)。
        stage_root = cfg["stage"].format(work=cfg["work"], base=base)
        stage = os.path.join(stage_root, name)
        mk = [os.path.join(HERE, "make_draft.py"), "--name", name,
              "--out-root", cfg["stage"], "--final-root", ROOT]
        for m in mp4s:
            mk += ["--clip", m]
        rc = sh(mk, "生成草稿", tail=3)
        if rc != 0:
            results.append({**r, "skipped": "draft-failed"})
            continue
        vr = sh([os.path.join(HERE, "validate_draft.py"), stage], "校验", tail=2)
        results.append({**r, "clips": len(mp4s), "mixed": use_voice,
                        "draft_name": name, "validate": vr})

        with open(f"_rebuild_{a.batch}.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)

    print(f"\n\n{'=' * 96}")
    print(f"{a.batch} 重建完成")
    print(f"{'=' * 96}")
    ok = [r for r in results if r.get("draft_name")]
    for r in results:
        flag = "OK " if r.get("draft_name") else "   "
        mx = "混音" if r.get("mixed") else "原声"
        print(f"  [{flag}] {r['file']:<26} {r.get('clips', '-'):>4} 片段  "
              f"{mx}  {r.get('draft_name') or r.get('skipped', '')}")
    print(f"\n成功 {len(ok)}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
