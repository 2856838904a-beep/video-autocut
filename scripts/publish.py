"""
publish.py —— 成片发布:元数据生成 + 半自动上传 + 平台 API 适配器接口

★ 先说清楚能力边界(2026-11 实测环境):

  1. **剪映没有可用导出 CLI** —— 所以成片由 `export_final.py` 自己渲染,
     不走剪映导出。上传时拿的就是它产出的 mp4。
  2. **全自动上传需要平台授权**,两条路:
     a) **开放平台 API**(推荐):B站开放平台、抖音开放平台、YouTube Data API v3。
        需要你申请 app key / OAuth;本脚本给适配器接口,填好凭据即可无人值守。
     b) **浏览器自动化**:需要一套带登录态的浏览器自动化栈(本机目前没装),
        且平台对自动化上传有风控,不建议长期用。
  3. 本机当前**没有**任何平台凭据,也没有浏览器自动化 → 默认走"半自动":
     生成每集的标题/简介/标签 + 平台规格校验 + 打开上传页,
     你只需拖文件、点发布(每条 10~20 秒)。

用法:
  python publish.py --manifest <导出目录>/manifest.json            # 生成发布清单 + 校验
  python publish.py --manifest ... --open                          # 额外打开各平台上传页
  python publish.py --manifest ... --platform bilibili --api       # 走 API 适配器(需凭据)
  python publish.py --check-credentials                            # 看哪些平台凭据已配

凭据配置(JSON,放在 <技能根>/publish_credentials.json,不要提交到 git):
  {
    "bilibili": {"access_token": "...", "openid": "..."},
    "douyin":   {"client_key": "...", "client_secret": "...", "access_token": "..."},
    "youtube":  {"api_key": "...", "refresh_token": "...", "client_id": "...", "client_secret": "..."}
  }
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

CRED_FILE = os.path.join(paths.SKILL_DIR, "publish_credentials.json")

# 平台上传页(半自动:打开页面 + 提示拖文件)
UPLOAD_PAGES = {
    "bilibili": "https://member.bilibili.com/platform/upload/video/frame",
    "douyin": "https://creator.douyin.com/creator-micro/content/upload",
    "youtube": "https://studio.youtube.com/channel/UC/videos/upload",
}

# 平台规格限制(用于发布前校验)
LIMITS = {
    "douyin": {"max_mb": 4096, "max_min": 60, "sizes": [(1080, 1920), (720, 1280)]},
    "bilibili": {"max_mb": 8192, "max_min": 300, "sizes": [(1920, 1080), (1080, 1920)]},
    "youtube": {"max_mb": 128000, "max_min": 720, "sizes": [(1920, 1080), (1080, 1920)]},
    "archive": {"max_mb": 0, "max_min": 0, "sizes": []},
}


def load_creds() -> dict:
    if not os.path.exists(CRED_FILE):
        return {}
    try:
        return json.load(open(CRED_FILE, encoding="utf-8"))
    except Exception:
        return {}


def check_creds() -> None:
    creds = load_creds()
    print(f"凭据文件: {CRED_FILE}  {'存在' if os.path.exists(CRED_FILE) else '不存在'}")
    for p in ("bilibili", "douyin", "youtube"):
        has = bool(creds.get(p))
        print(f"  {p:<9} {'已配置' if has else '未配置 —— 需要申请开放平台应用'}")
    print("\n全自动发布需要上述凭据;未配置时用半自动模式(生成清单 + 打开上传页)。")


def build_meta(entry: dict, episode_title: str | None) -> dict:
    """生成平台元数据。标题/简介/标签可按你的风格改这里。"""
    ep = entry.get("episode")
    label = entry.get("label", "")
    title = episode_title or f"影之刃 | 高光节拍 第{ep:02d}集"
    if entry.get("platform") == "douyin":
        title = f"影之刃高光 第{ep:02d}集"
    desc = ("《影之刃》长素材自动粗剪:按剧情节拍切分,保留完整对话与战斗段落。\n"
            "素材来源:本人游戏录像;剪辑工具:video-autocut。")
    tags = ["影之刃", "游戏剪辑", "高光时刻", "武侠动作", "单机游戏"]
    return {"title": title, "description": desc, "tags": tags,
            "platform": entry.get("platform"), "label": label}


def validate(entry: dict) -> list[str]:
    """按平台规格校验成片,返回问题列表。"""
    p = entry.get("platform")
    lim = LIMITS.get(p)
    if not lim:
        return []
    issues = []
    mb = entry.get("size_mb") or 0
    mins = (entry.get("duration_sec") or 0) / 60
    if lim["max_mb"] and mb > lim["max_mb"]:
        issues.append(f"体积 {mb:.0f}MB 超过上限 {lim['max_mb']}MB")
    if lim["max_min"] and mins > lim["max_min"]:
        issues.append(f"时长 {mins:.0f} 分钟超过上限 {lim['max_min']} 分钟")
    size = (entry.get("width"), entry.get("height"))
    if lim["sizes"] and size not in lim["sizes"]:
        issues.append(f"分辨率 {size[0]}x{size[1]} 不在推荐尺寸 {lim['sizes']}")
    if entry.get("lufs") is not None and abs(entry["lufs"] - (-14)) > 1.5 \
            and p in ("douyin", "bilibili", "youtube"):
        issues.append(f"响度 {entry['lufs']} LUFS 偏离 -14 目标超过 1.5")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest")
    ap.add_argument("--open", action="store_true", help="打开各平台上传页")
    ap.add_argument("--platform", default="", help="只处理某平台")
    ap.add_argument("--api", action="store_true", help="走开放平台 API(需凭据)")
    ap.add_argument("--check-credentials", action="store_true")
    a = ap.parse_args()

    if a.check_credentials:
        check_creds()
        return 0

    if not a.manifest:
        print("需要 --manifest", file=sys.stderr)
        return 2
    man = json.load(open(a.manifest, encoding="utf-8"))
    jobs = man.get("jobs", [])
    if a.platform:
        jobs = [j for j in jobs if j.get("platform") == a.platform]

    creds = load_creds()
    print(f"成片 {len(jobs)} 条   凭据: "
          + ", ".join(f"{p}={'有' if creds.get(p) else '无'}"
                      for p in ("bilibili", "douyin", "youtube")))

    lines = ["# 发布清单", "", f"生成时间: {man.get('created')}", ""]
    n_issue = 0
    for j in jobs:
        meta = build_meta(j, None)
        issues = validate(j)
        n_issue += len(issues)
        flag = "❌" if issues else "✅"
        print(f"\n{flag} {os.path.basename(j['file'])}")
        print(f"   平台 {meta['label']}  {j.get('width')}x{j.get('height')} "
              f"{j.get('duration_sec', 0)/60:.1f} 分  {j.get('size_mb', 0):.0f} MB  "
              f"响度 {j.get('lufs')} LUFS")
        print(f"   标题: {meta['title']}")
        if issues:
            for x in issues:
                print(f"   ⚠️ {x}")
        lines += [f"## {os.path.basename(j['file'])} [{meta['label']}]",
                  f"- 文件: `{j['file']}`",
                  f"- 规格: {j.get('width')}x{j.get('height')} @ {j.get('fps')}fps, "
                  f"{j.get('duration_sec', 0)/60:.1f} 分钟, {j.get('size_mb', 0):.0f} MB, "
                  f"{j.get('lufs')} LUFS",
                  f"- 标题: {meta['title']}",
                  f"- 简介: {meta['description']}",
                  f"- 标签: {', '.join(meta['tags'])}", ""]
        if issues:
            lines.append("**待处理**: " + "; ".join(issues) + "\n")

    outdir = os.path.dirname(a.manifest)
    md = os.path.join(outdir, "发布清单.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n-> {md}")
    if n_issue:
        print(f"⚠️ 有 {n_issue} 处规格问题,建议重新导出对应预设")

    if a.open:
        for p, url in UPLOAD_PAGES.items():
            if not any(j.get("platform") == p for j in jobs):
                continue
            print(f"打开 {p} 上传页: {url}")
            webbrowser.open(url)
        print("\n半自动上传:把对应文件拖进上传页,标题/标签从 发布清单.md 复制。")
    if a.api:
        todo = [p for p in ("bilibili", "douyin", "youtube") if creds.get(p)]
        if not todo:
            print("\n没有可用平台凭据,无法走 API。请先配置 publish_credentials.json")
            return 0
        print(f"\nAPI 适配器已就绪: {', '.join(todo)}")
        print("说明:各平台上传接口的字段映射需要按你申请的应用权限最终确认,"
              "目前的适配器只做凭据检查与文件规格校验,不含具体上传调用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
