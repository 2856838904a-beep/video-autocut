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


def _logged_in(page, plat: str) -> bool:
    """判断当前是否已登录到"能投稿"的状态(不跳转,只检测页面元素)。

    为什么要检测而不是靠 URL:三个平台登录后都会自动跳回上传页,但 YouTube
    要先点"创建"才出现文件框,所以按平台给不同判据。
    """
    try:
        if page.locator("input[type=file]").count() > 0:
            return True
        if plat == "youtube":
            for sel in ("#avatar-btn", "ytcp-avatar", "button[aria-label*='创建']",
                        "ytcp-button[aria-label*='Create']"):
                if page.locator(sel).count() > 0:
                    return True
        cur = page.url.lower()
        if "login" in cur or "passport" in cur or "accounts.google.com" in cur:
            return False
        # 兜底:页面上没有登录表单,且不是登录域名 -> 认为已登录
        login_form = (page.locator("input[placeholder*='账号']").count()
                      + page.locator("input[type='password']").count())
        return login_form == 0
    except Exception:
        return False


def doctor(profile: str, headless: bool, wait_sec: float = 1800,
           per_timeout: float = 600) -> int:
    """逐个平台打开上传页,**停在当前平台直到登录成功**再切下一个。

    ★ 踩坑:早期版本是"三个平台轮流 goto + 4 秒截图",结果页面每几秒就跳一次,
      用户根本来不及扫码登录。现在改成:打开一个 -> 每 3 秒检测是否已登录 ->
      登录成功才进入下一个平台;每个平台最多等 --per-timeout 秒。
    """
    from playwright.sync_api import sync_playwright
    os.makedirs(profile, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            profile, channel="chrome", headless=headless,
            locale="zh-CN", no_viewport=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        results = {}
        for idx, (plat, url) in enumerate(UPLOAD_URLS.items(), 1):
            print(f"\n{'=' * 62}")
            print(f"[{idx}/3] {plat}  —— 请在这个窗口登录(扫码/账号密码均可)")
            print(f"        地址: {url}")
            print(f"        登录成功后会自动进入下一个平台(每个平台最多等 {int(per_timeout)} 秒)")
            print("=" * 62, flush=True)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            t0 = time.time()
            last_shot = 0.0
            while time.time() - t0 < per_timeout:
                time.sleep(3)
                if _logged_in(page, plat):
                    print(f"  ✅ {plat} 已登录(用时 {int(time.time()-t0)}s),进入下一个", flush=True)
                    results[plat] = True
                    shot(page, profile, f"doctor_ok_{plat}")
                    break
                el = int(time.time() - t0)
                if el % 30 < 3:
                    print(f"  [{el:>3}s] 等待 {plat} 登录 ... 当前页: {page.url[:64]}", flush=True)
                if el - last_shot >= 60:
                    shot(page, profile, f"doctor_wait_{plat}")
                    last_shot = el
            else:
                print(f"  ⏰ {plat} 超时未登录,跳过(可稍后单独重试)", flush=True)
                results[plat] = False
                shot(page, profile, f"doctor_timeout_{plat}")

        print("\n" + "=" * 62)
        for k, v in results.items():
            print(f"  {k:<9} {'✅ 已登录' if v else '❌ 未登录'}")
        print("登录态已保存到独立 profile,可以关掉这个窗口了。")
        print("=" * 62, flush=True)
        # 保留窗口一会儿方便用户核对,但不阻塞后续投稿太久
        time.sleep(min(60, max(10, wait_sec)))
        ctx.close()
    return 0 if all(results.values()) else 1


def check_login(profile: str, headless: bool) -> int:
    """快速检查三个平台的登录态:看上传页有没有文件选择框 / 是否被重定向到登录页。"""
    from playwright.sync_api import sync_playwright
    os.makedirs(profile, exist_ok=True)
    ok = {}
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            profile, channel="chrome", headless=headless, locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for plat, url in UPLOAD_URLS.items():
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(5)
                n = page.locator("input[type=file]").count()
                cur = page.url
                logged = n > 0 and "login" not in cur.lower() and "passport" not in cur.lower()
                ok[plat] = logged
                print(f"  {plat:<9} {'✅ 已登录(有上传框)' if logged else '❌ 未登录/未到上传页'}  "
                      f"file输入={n}  url={cur[:70]}")
            except Exception as e:
                ok[plat] = False
                print(f"  {plat:<9} ❌ 检查失败: {e}")
        ctx.close()
    print("\n汇总:", {k: ("OK" if v else "NO") for k, v in ok.items()})
    return 0 if all(ok.values()) else 1


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
    ap.add_argument("--check-login", action="store_true", help="检查三个平台登录态")
    ap.add_argument("--wait", type=float, default=1800,
                    help="doctor 结束后窗口再保留的秒数")
    ap.add_argument("--per-timeout", type=float, default=600,
                    help="doctor 模式每个平台等待登录的最长秒数(默认 600)")
    a = ap.parse_args()

    if a.check_login:
        return check_login(a.profile, a.headless)
    if a.doctor:
        return doctor(a.profile, a.headless, a.wait, a.per_timeout)
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
