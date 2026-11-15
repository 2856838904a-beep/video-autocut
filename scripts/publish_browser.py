"""
publish_browser.py —— 用浏览器自动化把成片投到各平台(Playwright + 本机 Chrome)

★ 为什么用"持久化用户目录"而不是 API:
  平台开放平台的投稿 API 需要开发者资质与审核(拿 key 周期长),而浏览器自动化
  直接复用你**已登录**的 Chrome 会话,零申请成本。代价:平台风控会检测自动化,
  所以本脚本默认是**半自动**——把文件、标题、简介、标签都填好,停在"发布"按钮前
  让你确认;只有加 --publish 才真的点发布。

★ 安全设计:
  · 独立 Chrome 用户目录(默认 D:\\autocut_work\\chrome-autocut-profile),
    不碰你日常浏览器;登录一次后长期有效
  · 每一步失败都落截图 + 文本 dump 到 <profile>/diag/,便于定位平台改版
  · 默认不点发布;--publish 需要你显式加

用法:
  # ① 首次:打开三个平台上传页并截图,让你在里面登录(登录态会留在独立 profile)
  python publish_browser.py doctor

  # ② 投一条(默认填好停在发布前)
  python publish_browser.py --platform bilibili \
         --file "G:\\...\\影之刃_EP01_B站_1920x1080.mp4" \
         --title "影之刃 | 高光节拍 第01集" --desc "..." --tags "影之刃,游戏剪辑"

  # ③ 确认无误后真发
  python publish_browser.py --platform bilibili --file ... --publish

  # ④ 批量:按 manifest 把某平台所有集依次投
  python publish_browser.py --platform bilibili --manifest "G:\\...\\manifest.json"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_PROFILE = r"D:\autocut_work\chrome-autocut-profile"

UPLOAD_URLS = {
    "bilibili": "https://member.bilibili.com/platform/upload/video/frame",
    "douyin": "https://creator.douyin.com/creator-micro/content/upload",
    "youtube": "https://studio.youtube.com/",
}

# 每个平台的表单定位候选(平台常改版,这里给多组 fallback)
SELECTORS = {
    "bilibili": {
        "file": ["input[type=file]"],
        "title": ["input[placeholder*='标题']", ".video-title input",
                  "input.input-val", "input[maxlength]"],
        "desc": ["div[contenteditable='true']", "textarea"],
        "tags": ["input[placeholder*='标签']", "input[placeholder*='回车']"],
        "publish": ["button:has-text('立即投稿')", "span:has-text('立即投稿')",
                    "button:has-text('投稿')"],
        "submit_ready": ["input[placeholder*='标题']", "input[placeholder*='标题']"],
    },
    "douyin": {
        "file": ["input[type=file]"],
        "title": ["input[placeholder*='标题']", "input[placeholder*='作品']",
                  "input[type=text]"],
        "desc": ["div[contenteditable='true']", "textarea"],
        "tags": ["input[placeholder*='标签']", "div[contenteditable='true']"],
        "publish": ["button:has-text('发布')", "div:has-text('发布')"],
    },
    "youtube": {
        "file": ["input[type=file]"],
        "title": ["#textbox", "div[aria-label*='Title']"],
        "desc": ["#textbox >> nth=1", "div[aria-label*='Description']"],
        "tags": [],
        "publish": ["#done-button", "ytcp-button#done-button"],
    },
}


def shot(page, profile: str, tag: str) -> str:
    d = os.path.join(profile, "diag")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.png")
    try:
        page.screenshot(path=p, full_page=False)
    except Exception:
        pass
    return p


def first_visible(page, candidates: list[str], timeout: float = 3.0):
    """依次尝试候选选择器,返回第一个可见元素。"""
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout * 1000)
            return loc
        except Exception:
            continue
    return None


def doctor(profile: str, headless: bool, wait_sec: float = 1800) -> int:
    """打开三个平台上传页并截图,给用户登录用。"""
    from playwright.sync_api import sync_playwright
    os.makedirs(profile, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            profile, channel="chrome", headless=headless,
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled",
                  "--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for plat, url in UPLOAD_URLS.items():
            print(f"\n打开 {plat}: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(4)
            p = shot(page, profile, f"doctor_{plat}")
            try:
                title = page.title()
            except Exception:
                title = "?"
            files = page.locator("input[type=file]").count()
            print(f"  页面标题: {title[:60]}")
            print(f"  input[type=file] 数量: {files}  截图: {p}")
        print("\n" + "=" * 60)
        print("请在这个 Chrome 窗口里依次登录三个平台(扫码/账号密码均可):")
        print("  1) B站   https://member.bilibili.com   → 右上角登录,扫码即可")
        print("  2) 抖音  https://creator.douyin.com    → 手机抖音扫码")
        print("  3) YouTube studio.youtube.com          → Google 账号登录")
        print(f"窗口会保留 {int(wait_sec)} 秒。**登录完请直接关掉这个窗口**")
        print("(登录态已写入独立 profile,关窗口不会丢,下次投稿直接可用)")
        print("=" * 60, flush=True)
        end = time.time() + wait_sec
        while time.time() < end:
            time.sleep(15)
            left = int(end - time.time())
            print(f"  窗口保留中,还剩 {left}s ...", flush=True)
        ctx.close()
    return 0


def fill_and_upload(page, plat: str, file: str, title: str, desc: str,
                    tags: str, profile: str) -> bool:
    sel = SELECTORS[plat]
    name = os.path.basename(file)
    print(f"  [{plat}] 页面: {page.url}")

    # 1) 上传文件
    f = first_visible(page, sel["file"], timeout=8)
    if not f:
        print("  ❌ 找不到文件选择框(可能未登录或页面改版)")
        print("     截图:", shot(page, profile, f"nofile_{plat}"))
        return False
    print(f"  选择文件: {name}")
    f.set_input_files(file)
    time.sleep(6)

    # 2) 标题
    if title:
        t = first_visible(page, sel["title"], timeout=12)
        if t:
            try:
                t.click()
                t.fill("")
                t.type(title, delay=15)
                print(f"  标题已填: {title}")
            except Exception as e:
                print(f"  ⚠️ 标题填写失败: {e}")
        else:
            print("  ⚠️ 找不到标题输入框")

    # 3) 简介
    if desc and sel["desc"]:
        d = first_visible(page, sel["desc"], timeout=6)
        if d:
            try:
                d.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.type(desc[:300], delay=8)
                print("  简介已填")
            except Exception as e:
                print(f"  ⚠️ 简介填写失败: {e}")

    # 4) 标签(B站/抖音:输入后回车)
    if tags and sel["tags"]:
        tg = first_visible(page, sel["tags"], timeout=4)
        if tg:
            try:
                tg.click()
                for tag in [x.strip() for x in tags.split(",") if x.strip()][:6]:
                    tg.type(tag, delay=15)
                    page.keyboard.press("Enter")
                    time.sleep(0.6)
                print(f"  标签已填: {tags}")
            except Exception as e:
                print(f"  ⚠️ 标签填写失败: {e}")

    print("  截图:", shot(page, profile, f"filled_{plat}"))
    return True


def do_upload(profile: str, plat: str, file: str, title: str, desc: str,
              tags: str, publish: bool, headless: bool, keep: float) -> int:
    from playwright.sync_api import sync_playwright
    os.makedirs(profile, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            profile, channel="chrome", headless=headless, locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled",
                  "--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(UPLOAD_URLS[plat], wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        ok = fill_and_upload(page, plat, file, title, desc, tags, profile)
        if not ok:
            ctx.close()
            return 2
        if publish:
            btn = first_visible(page, SELECTORS[plat]["publish"], timeout=8)
            if not btn:
                print("  ❌ 找不到发布按钮,请手动点。截图:", shot(page, profile, f"nobtn_{plat}"))
                ctx.close()
                return 3
            print("  ⏳ 2 秒后点发布 ...")
            time.sleep(2)
            try:
                btn.click()
                print("  ✅ 已点发布,等待 15 秒确认结果")
                time.sleep(15)
                print("  截图:", shot(page, profile, f"published_{plat}"))
            except Exception as e:
                print(f"  ❌ 点发布失败: {e}")
                ctx.close()
                return 4
        else:
            print(f"\n  ⏸  已填好,但**没有点发布** —— 请在浏览器窗口里检查后手动发布。")
            print(f"     浏览器保持 {int(keep)} 秒(可用 --keep 调)。")
            end = time.time() + keep
            while time.time() < end:
                time.sleep(5)
        ctx.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=list(UPLOAD_URLS))
    ap.add_argument("--file")
    ap.add_argument("--title", default="")
    ap.add_argument("--desc", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--manifest", help="按 manifest 批量投某平台(从文件名推标题)")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--publish", action="store_true", help="真的点发布(默认只填不点)")
    ap.add_argument("--keep", type=float, default=180, help="不发布时浏览器保留秒数")
    ap.add_argument("--doctor", action="store_true", help="打开三个平台页面供登录")
    ap.add_argument("--wait", type=float, default=1800,
                    help="doctor 模式窗口保留秒数(默认 1800)")
    a = ap.parse_args()

    if a.doctor:
        return doctor(a.profile, a.headless, a.wait)
    if not a.platform:
        print("需要 --platform(或 --doctor)", file=sys.stderr)
        return 2

    if a.manifest:
        man = json.load(open(a.manifest, encoding="utf-8"))
        jobs = [j for j in man.get("jobs", []) if j.get("platform") == a.platform]
        if not jobs:
            print(f"manifest 里没有 {a.platform} 的成片")
            return 2
        rc = 0
        for j in jobs:
            ep = j.get("episode") or 0
            title = f"影之刃 | 高光节拍 第{ep:02d}集"
            print(f"\n=== 投 {os.path.basename(j['file'])} ===")
            rc = do_upload(a.profile, a.platform, j["file"], title, a.desc,
                           a.tags or "影之刃,游戏剪辑,高光时刻", a.publish,
                           a.headless, a.keep)
            if rc != 0:
                print("  中断批量")
                break
        return rc

    if not a.file:
        print("需要 --file(或 --manifest)", file=sys.stderr)
        return 2
    return do_upload(a.profile, a.platform, a.file, a.title, a.desc, a.tags,
                     a.publish, a.headless, a.keep)


if __name__ == "__main__":
    raise SystemExit(main())
