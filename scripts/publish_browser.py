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


def dismiss_dialogs(page, profile: str = "", tag: str = "") -> int:
    """关掉平台的各种"投稿须知/公告/引导"弹窗。

    ★ 踩坑:B站投稿页首次进入会弹 `videoup-notification-dialog` 遮罩,
      不关掉的话后续 click 会被拦截(报 "subtree intercepts pointer events")。
    """
    closed = 0
    texts = ["我知道了", "我已知晓", "知道了", "确定", "关闭", "下次再说",
             "同意", "继续", "开始投稿", "取消"]
    for t in texts:
        try:
            loc = page.locator(f"button:has-text('{t}'), span:has-text('{t}'), "
                               f"div[class*='btn']:has-text('{t}')").first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=2500)
                closed += 1
                time.sleep(0.6)
        except Exception:
            continue
    # 兜底:直接点遮罩上的关闭图标
    for sel in (".bcc-dialog__close", "[class*='close-icon']", "i[class*='close']",
                ".bcc-dialog__footer button"):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=2000, force=True)
                closed += 1
                time.sleep(0.5)
        except Exception:
            continue
    # 再兜底:按 ESC
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    if closed and profile:
        print(f"  已关闭 {closed} 个弹窗")
    return closed


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
    """判断是否已登录到"能投稿"的状态 —— 只认**正向证据**,不再用"没找到登录框"兜底。

    ★ 踩坑:早期版本兜底逻辑是"页面上没有登录表单就算已登录",结果抖音登录页
      (手机号输入框不是 password 类型)被误判成已登录,3 秒就"通过"了。
      现在只认三种正向证据:上传文件框 / 平台后台特征元素 / 明确的登录后 URL。
    """
    try:
        cur = page.url.lower()
        # 明确处在登录/授权域名 -> 一定没登录
        for bad in ("passport.bilibili.com", "accounts.google.com",
                    "/login", "passport.douyin", "sso"):
            if bad in cur:
                return False
        # 正向证据 1:上传文件框(三个平台进入投稿页后都有)
        if page.locator("input[type=file]").count() > 0:
            return True
        # 正向证据 2:平台后台特征元素
        if plat == "youtube":
            for sel in ("#avatar-btn", "ytcp-avatar", "button[aria-label*='创建']",
                        "ytcp-button[aria-label*='Create']", "#create-icon"):
                if page.locator(sel).count() > 0:
                    return True
            if "studio.youtube.com/channel/" in cur:
                return True
        if plat == "bilibili":
            # 登录后 B站会把页面重定向到「创作中心后台」(member.bilibili.com/platform/...),
            # 而不是停在投稿页 —— 所以先看域名,再看后台特征
            if "member.bilibili.com/platform" in cur and "passport" not in cur:
                return True
            for sel in (".nav-user-info", ".user-info", "a[href*='/platform/home']",
                        ".header-avatar", "text=创作中心", "text=投稿"):
                try:
                    if page.locator(sel).count() > 0:
                        return True
                except Exception:
                    pass
        if plat == "douyin":
            # 登录页特征词:扫码登录/验证码登录;出现这些一定没登录
            for bad_txt in ("扫码登录", "验证码登录"):
                try:
                    if page.locator(f"text={bad_txt}").count() > 0:
                        return False
                except Exception:
                    pass
            for sel in ("div[class*='avatar']", "img[class*='avatar']",
                        "text=发布视频", "text=作品管理", "text=数据中心"):
                try:
                    if page.locator(sel).count() > 0:
                        return True
                except Exception:
                    pass
        return False
    except Exception:
        return False


def doctor(profile: str, headless: bool, wait_sec: float = 1800,
           per_timeout: float = 600, only: str = "") -> int:
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
        items = [(k, v) for k, v in UPLOAD_URLS.items() if not only or k == only]
        for idx, (plat, url) in enumerate(items, 1):
            print(f"\n{'=' * 62}")
            print(f"[{idx}/{len(items)}] {plat}  —— 请在这个窗口登录(扫码/账号密码均可)")
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


def pick_dropdown(page, placeholder_kw: str, want: str, profile: str = "",
                  tag: str = "") -> bool:
    """点开某个下拉框并选含 want 文本的选项(用于 B站的「创作声明」「分区」)。

    B站投稿必填项里,「创作声明」是下拉选择,不选的话点发布会被拦。
    """
    try:
        box = page.locator(
            f"div:has-text('{placeholder_kw}') >> xpath=.. >> "
            f"[class*='select'], [class*='select']:has-text('{placeholder_kw}')").first
        # 更稳:直接点带 placeholder 文案的可点击区域
        cand = page.get_by_text(placeholder_kw, exact=False).first
        if cand.count() == 0:
            return False
        cand.click(timeout=3000)
        time.sleep(0.8)
        opt = page.locator(f"text={want}").first
        if opt.count() == 0:
            # 退而求其次:选第一个看起来"无需声明"的选项
            for alt in ("无需声明", "内容无需", "未使用AI", "不使用AI", "其他"):
                o = page.locator(f"text={alt}").first
                if o.count() > 0:
                    o.click(timeout=2500)
                    print(f"  下拉「{placeholder_kw}」按兜底选了: {alt}")
                    return True
            print(f"  ⚠️ 下拉「{placeholder_kw}」无匹配选项(需要: {want})")
            return False
        opt.click(timeout=2500)
        print(f"  下拉「{placeholder_kw}」已选: {want}")
        return True
    except Exception as e:
        print(f"  ⚠️ 下拉「{placeholder_kw}」选择失败: {e}")
        return False


def fill_and_upload(page, plat: str, file: str, title: str, desc: str,
                    tags: str, profile: str, upload_wait: float = 180,
                    claim: str = "", zone: str = "") -> bool:
    sel = SELECTORS[plat]
    name = os.path.basename(file)
    print(f"  [{plat}] 页面: {page.url}")

    # 1) 上传文件
    # ★ input[type=file] 在 B站/抖音都是**隐藏元素**(display:none),界面上是样式化按钮,
    #   所以不能用"可见"判据找它 —— 直接定位并 set_input_files 即可(hidden input 也支持)。
    dismiss_dialogs(page, profile, f"pre_{plat}")
    f = None
    for sel_one in sel["file"]:
        try:
            loc = page.locator(sel_one).first
            if loc.count() > 0:
                f = loc
                break
        except Exception:
            continue
    if f is None:
        print("  ❌ 找不到文件选择框(可能未登录或页面改版)")
        print("     截图:", shot(page, profile, f"nofile_{plat}"))
        return False
    print(f"  选择文件: {name}")
    f.set_input_files(file)
    print(f"  已提交文件,等待页面出现填写框(最多 {int(upload_wait)}s;大文件含上传+转码)")
    t0 = time.time()
    ready = None
    while time.time() - t0 < upload_wait:
        time.sleep(3)
        ready = first_visible(page, sel["title"], timeout=1.0)
        if ready:
            break
        if int(time.time() - t0) % 20 < 3:
            print(f"    [{int(time.time()-t0):>3}s] 等待上传/转码 ...", flush=True)
    if not ready:
        print("  ⚠️ 超时未见标题框(可能还在上传),截图:", shot(page, profile, f"uploadwait_{plat}"))
        return False

    dismiss_dialogs(page, profile, f"mid_{plat}")
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

    # 5) B站必填:创作声明 / 分区
    if plat == "bilibili":
        if claim:
            pick_dropdown(page, "创作声明", claim, profile, plat)
        if zone:
            pick_dropdown(page, "分区", zone, profile, plat)

    print("  截图:", shot(page, profile, f"filled_{plat}"))
    return True


def do_upload(profile: str, plat: str, file: str, title: str, desc: str,
              tags: str, publish: bool, headless: bool, keep: float,
              upload_wait: float = 180) -> int:
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
        ok = fill_and_upload(page, plat, file, title, desc, tags, profile,
                             upload_wait, claim, zone)
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
    ap.add_argument("--claim", default="", help="B站创作声明(如 '内容无需声明')")
    ap.add_argument("--zone", default="游戏", help="B站分区(默认 游戏)")
    ap.add_argument("--upload-wait", type=float, default=180,
                    help="提交文件后等待上传/转码完成的最长秒数")
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
        return doctor(a.profile, a.headless, a.wait, a.per_timeout, a.platform or "")
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
                           a.headless, a.keep, a.upload_wait, a.claim, a.zone)
            if rc != 0:
                print("  中断批量")
                break
        return rc

    if not a.file:
        print("需要 --file(或 --manifest)", file=sys.stderr)
        return 2
    return do_upload(a.profile, a.platform, a.file, a.title, a.desc, a.tags,
                     a.publish, a.headless, a.keep, a.upload_wait, a.claim, a.zone)


if __name__ == "__main__":
    raise SystemExit(main())
