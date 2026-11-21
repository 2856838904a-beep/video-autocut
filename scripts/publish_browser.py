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

DEFAULT_PROFILE = os.environ.get("AUTOCUT_CHROME_PROFILE",
                               r"D:\autocut_work\chrome-autocut-profile")

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
        "title": ["input[placeholder*='填写作品标题']", "input[placeholder*='作品']"],
        "desc": ["div.zone-container", "div[contenteditable='true'].editor-kit-container",
                 "div[contenteditable='true']"],
        "tags": [],
        "publish": ["button:has-text('发布')", "button.button-dhlUZE.primary-cECiOJ"],
        "private": ["label:has-text('仅自己可见')", "text=仅自己可见"],
        "ready": ["text=上传成功"],
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


def dump_options(profile: str, plat: str, headless: bool,
                 keywords: list[str]) -> int:
    """打开投稿页,点开各必填下拉,把选项文案打印出来(不依赖猜测)。

    用途:B站「创作声明」「分区」这类下拉的文案会随平台改版,
    先 dump 再决定 --claim/--zone 传什么。
    """
    from playwright.sync_api import sync_playwright
    os.makedirs(profile, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            profile, channel="chrome", headless=headless, locale="zh-CN",
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(UPLOAD_URLS[plat], wait_until="domcontentloaded", timeout=60000)
        time.sleep(6)
        dismiss_dialogs(page, profile, f"dump_{plat}")
        for kw in keywords:
            print(f"\n--- 下拉「{kw}」---")
            try:
                page.get_by_text(kw, exact=False).first.click(timeout=4000)
                time.sleep(1.2)
                # 选项一般是 li / [class*=option] / 可见文本
                opts = []
                for sel in ("li", "[class*='option']", "[role='option']"):
                    loc = page.locator(sel)
                    n = min(loc.count(), 40)
                    for i in range(n):
                        try:
                            e = loc.nth(i)
                            if not e.is_visible():
                                continue
                            t = (e.inner_text() or "").strip().replace("\n", " ")
                            if t and len(t) <= 30 and t not in opts:
                                opts.append(t)
                        except Exception:
                            continue
                print(f"  共 {len(opts)} 个可见候选:")
                for t in opts[:30]:
                    print(f"    · {t}")
                page.keyboard.press("Escape")
                time.sleep(0.5)
            except Exception as e:
                print(f"  打开失败: {e}")
        shot(page, profile, f"dump_{plat}")
        ctx.close()
    return 0


def dump_form(profile: str, plat: str, file: str, headless: bool,
              keywords: list[str], upload_wait: float = 240) -> int:
    """先选一个文件让投稿表单出现,再把各必填下拉的可选项文案打出来。

    为什么必须带文件:B站/抖音的「创作声明」「分区」等字段**只在选中视频后渲染**,
    空页面 dump 不到。用几秒的小片段即可,无需真实成片。
    """
    from playwright.sync_api import sync_playwright
    os.makedirs(profile, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            profile, channel="chrome", headless=headless, locale="zh-CN",
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(UPLOAD_URLS[plat], wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        dismiss_dialogs(page, profile, f"dumpform_{plat}")
        f = page.locator("input[type=file]").first
        if f.count() == 0:
            print("  ❌ 没有文件输入框(未登录?)")
            ctx.close()
            return 2
        f.set_input_files(file)
        print(f"  已选择测试文件 {os.path.basename(file)},等表单出现 ...")
        sel = SELECTORS[plat]
        t0 = time.time()
        while time.time() - t0 < upload_wait:
            time.sleep(2)
            if first_visible(page, sel["title"], timeout=1.0):
                break
        dismiss_dialogs(page, profile, f"dumpform2_{plat}")
        for kw in keywords:
            print(f"\n--- 下拉「{kw}」的可选项 ---")
            try:
                page.get_by_text(kw, exact=False).first.click(timeout=5000)
                time.sleep(1.2)
                opts = []
                for one in ("li", "[class*='option']", "[role='option']"):
                    loc = page.locator(one)
                    for i in range(min(loc.count(), 50)):
                        try:
                            e = loc.nth(i)
                            if not e.is_visible():
                                continue
                            t = (e.inner_text() or "").strip().replace("\n", " ")
                            if t and len(t) <= 40 and t not in opts:
                                opts.append(t)
                        except Exception:
                            continue
                print(f"  {len(opts)} 个候选:")
                for t in opts[:40]:
                    print(f"    · {t}")
                page.keyboard.press("Escape")
                time.sleep(0.5)
            except Exception as e:
                print(f"  打开失败: {e}")
                page.keyboard.press("Escape")
        shot(page, profile, f"dumpform_{plat}")
        print("\n截图:", shot(page, profile, f"dumpform_done_{plat}"))
        ctx.close()
    return 0


def select_cover(page, profile: str = "", tag: str = "",
                 wait_sec: float = 120) -> bool:
    """选择封面(B站必填,不选点投稿会被拦)。

    实测要点:
      · 封面区: .cover-empty(添加封面) + .cover-recommend-list(系统推荐帧)
      · **推荐帧是视频上传后异步生成的**,刚上传完可能只有「AI生成」占位,
        要等 .img-item-cover 出现再点(直接点会 no-op)
      · 选中后 .cover-empty 会被封面图替换
    """
    import time as _t
    try:
        if page.locator(".cover-empty").count() == 0:
            print("  封面已存在")
            return True
        # 已上传文件但推荐帧可能还没生成 —— 先等
        print("  等待系统推荐封面帧 ...")
        t0 = _t.time()
        got = False
        while _t.time() - t0 < wait_sec:
            if page.locator(".img-item-cover").count() > 0:
                got = True
                break
            _t.sleep(3)
        if not got:
            print("  ⚠️ 推荐帧未生成,尝试打开「添加封面」")
        # 点第一张推荐帧
        for sel in (".cover-recommend-list .img-item-cover",
                    ".img-item-cover",
                    ".cover-recommend-list .card .img-item-box"):
            loc = page.locator(sel).first
            try:
                if loc.count() > 0:
                    loc.click(timeout=6000)
                    _t.sleep(2.5)
                    if page.locator(".cover-empty").count() == 0:
                        print("  ✅ 封面已选(系统推荐帧)")
                        shot(page, profile, f"cover_ok_{tag}")
                        return True
                    print("  点了 {} 但封面仍未设置,截图: {}".format(
                        sel, shot(page, profile, f"cover_try_{tag}")))
            except Exception as e:
                print("  点击封面失败", str(e)[:70])
        print("  ⚠️ 封面选择失败(投稿会被拦)")
        shot(page, profile, f"coverfail_{tag}")
        return False
    except Exception as e:
        print(f"  ⚠️ 封面选择异常: {e}")
        return False


def select_claim(page, want: str, profile: str = "", tag: str = "") -> bool:
    """B站「创作声明」必填下拉。

    实测结构(2026-11):
      · 声明在「更多设置（含声明与权益…）」折叠区里,先点开
      · 控件: .creation-statement-container [class*='select']
      · 选项渲染成 .bcc-option,文案例如「内容为自制：未经作者允许，禁止转载」
    """
    try:
        # 1) 展开更多设置
        try:
            page.get_by_text("更多设置", exact=False).first.click(timeout=3000)
            time.sleep(1.0)
        except Exception:
            pass
        # 2) 点开声明下拉
        box = page.locator(".creation-statement-container [class*='select']").first
        if box.count() == 0:
            print("  ⚠️ 找不到创作声明下拉")
            return False
        box.click(timeout=5000)
        time.sleep(1.5)
        # 等选项列表真的渲染出来(没渲染就点,等于没点)
        for _ in range(10):
            if page.locator(".bcc-option").count() > 0:
                break
            time.sleep(0.5)
        # 3) 选项:实测 B站 选项是 <article> 包裹(「内容为自制」还在独立的授权声明分组里),
        #    点 .bcc-option 往往无效或选错,必须点 article
        def _val():
            try:
                return page.evaluate("""() => {
                    const c = document.querySelector('.creation-statement-container');
                    const i = c ? c.querySelector('input') : null;
                    return i ? (i.value || '') : '';
                }""")
            except Exception:
                return ""
        for sel in (f"article:has-text('{want}')",
                    f".creation-statement-container article:has-text('{want[:8]}')",
                    f".bcc-option:has-text('{want}')",
                    f"text={want}"):
            opt = page.locator(sel).first
            try:
                if opt.count() > 0:
                    opt.click(timeout=3000, force=True)
                    time.sleep(1.2)
                    v = _val()
                    if v:
                        print(f"  创作声明已选并校验(input.value): {v}")
                        shot(page, profile, f"claim_{tag}")
                        return True
                    print("  点击了选项但 input.value 仍为空,重试")
            except Exception:
                continue
        # 兜底:选「内容为自制」/「内容无需标注」
        for alt in ("内容为自制", "内容无需标注"):
            for sel in (f"article:has-text('{alt}')", f".bcc-option:has-text('{alt}')"):
                try:
                    opt = page.locator(sel).first
                    if opt.count() > 0:
                        opt.click(timeout=3000, force=True)
                        time.sleep(1.0)
                        v = _val()
                        if v:
                            print(f"  创作声明按兜底选了: {v}")
                            return True
                except Exception:
                    continue
        print("  ⚠️ 没选中创作声明")
        return False
    except Exception as e:
        print(f"  ⚠️ 创作声明选择异常: {e}")
        return False


def wait_upload_done(page, max_sec: float = 1800, profile: str = "",
                     tag: str = "") -> bool:
    """等视频**上传+转码完成**再点投稿/存草稿。

    ★ 踩坑:大文件(995MB)选中文件几秒后页面就出现标题框(其实还在后台传),
      此时点「存草稿」不会生效、页面也不报错 —— 必须等进度条消失。
      进度条形态: "62.7MB/948.8MB  当前速度 6.8MB/s  剩余 2.2 分钟"
    """
    import re as _re
    import time as _t
    t0 = _t.time()
    last = ""
    while _t.time() - t0 < max_sec:
        try:
            txt = page.locator("body").inner_text()
        except Exception:
            txt = ""
        m = _re.search(r"(\d+(?:\.\d+)?)\s*MB\s*/\s*(\d+(?:\.\d+)?)\s*MB", txt)
        if m:
            cur, tot = float(m.group(1)), float(m.group(2))
            pct = cur / tot * 100 if tot else 0
            line = f"  上传中 {pct:5.1f}%  ({cur:.0f}/{tot:.0f} MB)  已用 {int(_t.time()-t0)}s"
            if line != last:
                print(line, flush=True)
                last = line
            _t.sleep(4)
            continue
        # 没有进度条了:再确认没有"转码/处理中"提示
        if ("转码" in txt or "处理中" in txt or "上传中" in txt):
            _t.sleep(4)
            continue
        print(f"  ✅ 上传/转码完成(等待 {int(_t.time()-t0)}s)")
        return True
    print(f"  ⚠️ 等待上传超时({int(max_sec)}s)")
    shot(page, profile, f"uploadtimeout_{tag}")
    return False


def wait_douyin_ready(page, size_mb: float = 0, max_sec: float = 900) -> bool:
    """等抖音视频上传/检测完成。

    ★ 踩坑1:页面静态文案含「上传中」(如"如作品还在上传中,请勿关闭页面"),不能当判据。
    ★ 踩坑2:顶部「上传成功」是 3 秒的瞬时提示,轮询容易错过。
    做法:读页面所有百分比;「检测中NN%」是封面检测,上传完成后会消失;
          再用文件大小粗估耗时(按 5MB/s)做下限,两者都满足才认为就绪。
    """
    import re as _re
    import time as _t
    expect = (size_mb / 5.0) if size_mb else 30.0
    expect = max(20.0, min(expect, 600.0))
    t0 = _t.time()
    while _t.time() - t0 < max_sec:
        el = _t.time() - t0
        try:
            txt = page.locator("body").inner_text()
        except Exception:
            txt = ""
        pcts = [int(x) for x in _re.findall(r"(\d{1,3})\s*%", txt)]
        detecting = "检测中" in txt
        if el % 20 < 3 or el < 6:
            print(f"    [{int(el):>3}s] 检测中={detecting} 百分比={pcts[:4]} "
                  f"(预计至少 {int(expect)}s)", flush=True)
        if el >= expect * 1.1 and not detecting and (not pcts or max(pcts) >= 99):
            print(f"  ✅ 抖音上传/检测完成(等待 {int(el)}s)")
            return True
        _t.sleep(4)
    print("  ⚠️ 抖音等待超时,仍尝试发布")
    return True


def douyin_publish(page, profile: str = "", timeout: float = 120) -> bool:
    """点发布并轮询是否成功(URL 跳转 / 成功提示)。失败时只重试一次,避免重复投稿。"""
    import time as _t
    t0 = _t.time()
    clicked = 0
    while _t.time() - t0 < timeout:
        try:
            btn = page.locator("button:has-text('发布')").last
            if btn.count() > 0 and clicked < 2:
                cls = (btn.get_attribute("class") or "").lower()
                if "disabled" not in cls:
                    btn.click(timeout=8000)
                    clicked += 1
                    print(f"  已点发布(第 {clicked} 次)")
                    _t.sleep(10)
        except Exception as e:
            print("  点发布异常:", str(e)[:60])
        try:
            url = page.url
            txt = page.locator("body").inner_text()
        except Exception:
            url, txt = "", ""
        if "content/manage" in url or "manage" in url:
            print("  ✅ 已跳转作品管理,发布提交成功")
            return True
        if ("发布成功" in txt) or ("作品已发布" in txt) or ("已发布" in txt and "暂存" not in txt):
            print("  ✅ 页面出现发布成功提示")
            return True
        _t.sleep(6)
    print("  ⚠️ 未检测到发布成功,截图留证")
    shot(page, profile, "dy_publish_uncertain")
    return False


def douyin_set_cover(page, cover_path: str, profile: str = "") -> bool:
    """抖音封面:页面有独立 input[type=file][accept*=image],塞图后在裁剪弹窗点保存。"""
    import time as _t
    if not cover_path or not os.path.exists(cover_path):
        return False
    try:
        fi = page.locator("input[type=file][accept*='image']").first
        if fi.count() == 0:
            print("  ⚠️ 找不到封面图片输入框")
            return False
        fi.set_input_files(cover_path)
        _t.sleep(3)
        for kw in ("保存", "确定", "确认", "完成", "Save"):
            try:
                b = page.locator(f"button:has-text('{kw}')").first
                if b.count() > 0 and b.is_visible():
                    b.click(timeout=3000)
                    print(f"  封面已设置(裁剪确认: {kw})")
                    _t.sleep(1.5)
                    return True
            except Exception:
                continue
        print("  封面已提交(未出现裁剪确认)")
        return True
    except Exception as e:
        print("  ⚠️ 封面设置失败:", str(e)[:70])
        return False


def douyin_fill(page, title: str, desc: str, tags: str, private: bool,
                profile: str, cover_path: str = "") -> bool:
    """抖音专用填写:标题(≤30字) + 简介(含话题) + 可见范围。"""
    import time as _t
    # 标题:抖音限制 30 字
    t = page.locator("input[placeholder*='填写作品标题']").first
    if t.count() == 0:
        t = page.locator("input[placeholder*='标题']").first
    if t.count() > 0:
        short = title if len(title) <= 30 else title[:30]
        t.click()
        t.fill("")
        t.type(short, delay=15)
        print(f"  标题({len(short)}字): {short}")
    else:
        print("  ⚠️ 抖音标题框未找到")
        return False

    # 简介 + 话题
    body_text = desc or ""
    tag_list = [x.strip() for x in (tags or "").split(",") if x.strip()]
    if tag_list:
        body_text = body_text + " " + " ".join("#" + t2 for t2 in tag_list[:5])
    if body_text:
        ed = page.locator("div.zone-container").first
        if ed.count() == 0:
            ed = page.locator("div[contenteditable='true']").first
        if ed.count() > 0:
            ed.click()
            _t.sleep(0.5)
            for line in body_text.split("\n"):
                page.keyboard.type(line[:200], delay=8)
                page.keyboard.press("Enter")
            print(f"  简介已填({len(body_text)}字,含 {len(tag_list)} 个话题)")

    # 可见范围:仅自己可见
    if private:
        picked = False
        for sel in ("label:has-text('仅自己可见')", "text=仅自己可见"):
            loc = page.locator(sel).first
            try:
                if loc.count() > 0:
                    loc.click(timeout=5000)
                    picked = True
                    print("  可见范围: 已选「仅自己可见」")
                    break
            except Exception as e:
                print("  选可见范围失败:", str(e)[:60])
        if not picked:
            print("  ⚠️ 未选中「仅自己可见」,请人工确认")
    # 封面(可选但强烈建议:发布时必填)
    if cover_path:
        douyin_set_cover(page, cover_path, profile)
    _t.sleep(0.5)
    shot(page, profile, "douyin_filled")
    return True


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

    # 5) B站必填:创作声明 + 封面(分区默认"游戏",但测试片段可能被判成别的)
    if plat == "bilibili" and zone:
        try:
            cur = page.locator(".form-item:has-text('分区')").first.inner_text()
            if zone not in cur:
                page.locator(".form-item:has-text('分区') [class*='select']").first.click(timeout=4000)
                time.sleep(1)
                opt = page.locator(".bcc-option:has-text('{}')".format(zone)).first
                if opt.count() > 0:
                    opt.click(timeout=3000)
                    print(f"  分区已设为: {zone}")
                    time.sleep(0.5)
        except Exception as e:
            print("  分区设置跳过:", str(e)[:60])
    # 5b) B站必填:创作声明 + 封面
    if plat == "bilibili":
        select_claim(page, claim or "内容为自制：未经作者允许，禁止转载", profile, plat)
        select_cover(page, profile, plat)

    print("  截图:", shot(page, profile, f"filled_{plat}"))
    return True


def do_upload(profile: str, plat: str, file: str, title: str, desc: str,
              tags: str, publish: bool, headless: bool, keep: float,
              upload_wait: float = 180, claim: str = "", zone: str = "",
              draft_mode: bool = False, private: bool = False,
              cover: str = "") -> int:
    a_draft_mode = draft_mode
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
        if plat == "douyin":
            # 抖音:选文件 -> 等表单 -> 专用填写 -> 等上传完成 -> 发布
            dismiss_dialogs(page, profile, "dy")
            # 上次未完成的视频会弹「继续编辑/放弃」,选放弃,避免混入旧草稿
            for kw in ("放弃", "放弃编辑"):
                try:
                    loc = page.get_by_text(kw, exact=True).first
                    if loc.count() > 0:
                        loc.click(timeout=3000)
                        print("  已放弃上次未发布的视频")
                        time.sleep(2)
                        break
                except Exception:
                    pass
            fi = page.locator("input[type=file]").first
            if fi.count() == 0:
                print("  ❌ 抖音找不到文件框(未登录?)")
                shot(page, profile, "dy_nofile")
                ctx.close()
                return 2
            print(f"  选择文件: {os.path.basename(file)}")
            fi.set_input_files(file)
            for _ in range(90):
                time.sleep(1)
                if page.locator("input[placeholder*='填写作品标题']").count() > 0:
                    break
            dismiss_dialogs(page, profile, "dy2")
            ok = douyin_fill(page, title, desc, tags, private, profile, cover)
            try:
                size_mb = os.path.getsize(file) / 1e6
            except OSError:
                size_mb = 0
            wait_douyin_ready(page, size_mb, upload_wait)
            if draft_mode:
                # 抖音兜底:存草稿(暂存离开),之后可在 App/网页里补封面再发
                clicked = False
                for sel in ("button:has-text('暂存离开')", "text=暂存离开"):
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            loc.scroll_into_view_if_needed()
                            loc.click(timeout=8000)
                            clicked = True
                            print("  💾 已点「暂存离开」(存草稿)")
                            break
                    except Exception as e:
                        print("  暂存失败:", str(e)[:60])
                time.sleep(8)
                shot(page, profile, "dy_draft_saved")
                ctx.close()
                return 0 if clicked else 3
            if publish:
                douyin_publish(page, profile)
                shot(page, profile, "dy_published")
            else:
                print("  ⏸  已填好未发布,浏览器保留 %ds" % int(keep))
                time.sleep(keep)
            ctx.close()
            return 0
        ok = fill_and_upload(page, plat, file, title, desc, tags, profile,
                             upload_wait, claim, zone)
        if not ok:
            ctx.close()
            return 2
        print("  等待上传/转码完成 ...")
        wait_upload_done(page, max_sec=1800, profile=profile, tag=plat)

        if a_draft_mode:
            # 私有验证: 存草稿(只有自己能看见,不公开)
            # 实测: 按钮在底部 submit-actions 里,文案是「存草稿」,需要滚动到底部
            print("  滚动到底部找「存草稿」...")
            page.keyboard.press("End")
            time.sleep(1.2)
            page.mouse.wheel(0, 4000)
            time.sleep(1.0)
            btn = None
            for sel in ("span.submit-button-anchor:has-text('存草稿')",
                        "div.submit-actions >> text=存草稿",
                        "text=存草稿",
                        "button:has-text('存草稿')"):
                loc = page.locator(sel).first
                try:
                    if loc.count() > 0:
                        btn = loc
                        print(f"  找到存草稿按钮: {sel}")
                        break
                except Exception:
                    continue
            if not btn:
                print("  ❌ 找不到「存草稿」按钮,截图:", shot(page, profile, f"nodraft_{plat}"))
                ctx.close()
                return 3
            try:
                btn.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
            btn.click(force=True, timeout=10000)
            print("  💾 已点存草稿,等待 15 秒")
            time.sleep(15)
            # 保存后 B站 会跳转到「稿件管理」,页面顶部有「草稿 N」——这是最直接的验证
            try:
                txt = page.locator("body").inner_text()
                import re as _re
                m = _re.search(r"草稿\s*(\d+)", txt)
                if m:
                    print(f"  ✅ 验证:稿件管理里「草稿」数量 = {m.group(1)}")
                else:
                    print("  页面未出现「草稿 N」计数,当前 URL:", page.url)
            except Exception as e:
                print("  草稿数量读取失败:", str(e)[:80])
            print("  截图:", shot(page, profile, f"draft_saved_{plat}"))
            # ---- 点击后立刻用 API 自检(最可靠的"是否成功") ----
            try:
                r = page.request.get(
                    "https://member.bilibili.com/x/web/archives?pn=1&ps=20&status=all",
                    timeout=20000)
                j = r.json()
                data = j.get("data") or {}
                cls = data.get("class") or {}
                print(f"  [API 自检] 已发布 {cls.get('pubed')} / "
                      f"未发布 {cls.get('not_pubed')} / 审核中 {cls.get('is_pubing')}")
                hits = [a.get("Archive", {}).get("title", "")
                        for a in (data.get("arc_audits") or [])
                        if a.get("Archive", {}).get("title")]
                print("  最近稿件:", " | ".join(hits[:3]) if hits else "(无)")
            except Exception as e:
                print("  [API 自检] 失败:", str(e)[:90])
            ctx.close()
            return 0
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


def verify_bili(profile: str, keyword: str, expect: int = 0,
                show: int = 30) -> int:
    """用 B站 创作中心内部 API 精确核对稿件状态(比看截图可靠)。

    实测可用的端点:
      GET https://member.bilibili.com/x/web/archives?pn=1&ps=50&status=all
      -> data.class {pubed,not_pubed,is_pubing}, data.arc_audits[].Archive{title,bvid,state,...}
    """
    from playwright.sync_api import sync_playwright
    os.makedirs(profile, exist_ok=True)
    found = []
    classes = {}
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            profile, channel="chrome", headless=True, locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://member.bilibili.com/platform/home",
                  wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        for status in ("all", "draft", "not_pubed", "is_pubing"):
            url = (f"https://member.bilibili.com/x/web/archives?"
                   f"pn=1&ps=50&status={status}")
            try:
                r = page.request.get(url, timeout=20000)
                j = r.json()
            except Exception as e:
                print(f"[{status}] 请求失败: {str(e)[:100]}")
                continue
            data = j.get("data") or {}
            cls = data.get("class") or {}
            if cls:
                classes = cls
            audits = data.get("arc_audits") or []
            page_info = data.get("page") or {}
            print(f"[{status}] code={j.get('code')} 总{page_info.get('count')}条 "
                  f"class={cls} 本页{len(audits)}条")
            for a in audits:
                arc = a.get("Archive") or {}
                title = arc.get("title") or ""
                if keyword and keyword not in title:
                    continue
                found.append({
                    "title": title,
                    "bvid": arc.get("bvid"),
                    "state": arc.get("state"),
                    "state_desc": arc.get("state_desc"),
                    "ctime": arc.get("ctime") or arc.get("pubtime"),
                    "reject": arc.get("reject_reason"),
                    "status": status,
                })
        ctx.close()

    print("\n=== 稿件分类统计 ===")
    print(f"  已发布 {classes.get('pubed')} / 未发布 {classes.get('not_pubed')} / "
          f"审核中 {classes.get('is_pubing')}")

    print(f"\n=== 匹配「{keyword}」的稿件 ({len(found)} 条) ===")
    for f in found[:show]:
        line = (f"  · {f['title'][:60]}\n"
                f"      bvid={f['bvid']} state={f['state']}({f['state_desc']}) "
                f"status={f['status']}")
        print(line)
        if f.get("reject"):
            print(f"      驳回原因: {str(f['reject'])[:120]}")
    if not found:
        print("  （没有匹配稿件）")

    if expect and len(found) < expect:
        print(f"\n❌ 期望至少 {expect} 条,实际 {len(found)} 条 —— 上传可能未成功")
        return 1
    if found:
        print(f"\n✅ 核对通过: {len(found)} 条稿件在账号里")
        return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=list(UPLOAD_URLS))
    ap.add_argument("--file")
    ap.add_argument("--title", default="")
    ap.add_argument("--desc", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--manifest", help="按 manifest 批量投某平台(从文件名推标题)")
    ap.add_argument("--meta", default="", help="批量模式用的定制文案 JSON")
    ap.add_argument("--only", default="", help="批量模式只投指定集号,如 2,3,4")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--publish", action="store_true", help="真的点发布(默认只填不点)")
    ap.add_argument("--verify", action="store_true", help="用 API 核对稿件状态(推荐)")
    ap.add_argument("--verify-keyword", default="影之刃")
    ap.add_argument("--verify-expect", type=int, default=0)
    ap.add_argument("--cover", default="", help="封面图片路径(抖音用)")
    ap.add_argument("--private", action="store_true",
                    help="私密上传(抖音:仅自己可见)")
    ap.add_argument("--draft", action="store_true",
                    help="保存草稿(私有验证:只有自己能看见)")
    ap.add_argument("--keep", type=float, default=180, help="不发布时浏览器保留秒数")
    ap.add_argument("--claim", default="", help="B站创作声明(如 '内容无需声明')")
    ap.add_argument("--zone", default="游戏", help="B站分区(默认 游戏)")
    ap.add_argument("--upload-wait", type=float, default=180,
                    help="提交文件后等待上传/转码完成的最长秒数")
    ap.add_argument("--doctor", action="store_true", help="打开三个平台页面供登录")
    ap.add_argument("--check-login", action="store_true", help="检查三个平台登录态")
    ap.add_argument("--dump-form", action="store_true",
                    help="先选文件让表单出现,再 dump 必填下拉选项")
    ap.add_argument("--dump-form-file", default="",
                    help="dump-form 用的小测试片段")
    ap.add_argument("--dump-options", action="store_true",
                    help="读取必填下拉的选项文案(配合 --platform)")
    ap.add_argument("--dump-keywords", default="创作声明,分区",
                    help="要 dump 的下拉文案,逗号分隔")
    ap.add_argument("--wait", type=float, default=1800,
                    help="doctor 结束后窗口再保留的秒数")
    ap.add_argument("--per-timeout", type=float, default=600,
                    help="doctor 模式每个平台等待登录的最长秒数(默认 600)")
    a = ap.parse_args()

    if a.verify:
        return verify_bili(a.profile, a.verify_keyword, a.verify_expect)
    if a.dump_form:
        kws = [x.strip() for x in a.dump_keywords.split(",") if x.strip()]
        f = a.dump_form_file or a.file
        if not f:
            print("需要 --dump-form-file 指向一个小视频", file=sys.stderr)
            return 2
        return dump_form(a.profile, a.platform or "bilibili", f, a.headless, kws)
    if a.dump_options:
        kws = [x.strip() for x in a.dump_keywords.split(",") if x.strip()]
        return dump_options(a.profile, a.platform or "bilibili", a.headless, kws)
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
        if a.only:
            want = {int(x) for x in a.only.split(",") if x.strip().isdigit()}
            jobs = [j for j in jobs if (j.get("episode") or 0) in want]
        meta_map = {}
        if a.meta and os.path.exists(a.meta):
            md = json.load(open(a.meta, encoding="utf-8"))
            meta_map = md.get("episodes", {})
        if not jobs:
            print(f"manifest 里没有 {a.platform} 的成片")
            return 2
        rc = 0
        for j in jobs:
            ep = j.get("episode") or 0
            title = f"影之刃 | 高光节拍 第{ep:02d}集"
            desc = a.desc
            tags = a.tags or "影之刃,游戏剪辑,高光时刻"
            m = meta_map.get(str(ep), {}).get(a.platform) or {}
            if m:
                title = m.get("title") or title
                desc = m.get("desc") or desc
                tags = m.get("tags") or tags
                print(f"  使用定制文案: {title[:40]}...")
            print(f"\n=== 投 {os.path.basename(j['file'])} ===")
            rc = do_upload(a.profile, a.platform, j["file"], title, desc,
                           tags, a.publish,
                           a.headless, a.keep, a.upload_wait, a.claim, a.zone,
                           a.draft, a.private, a.cover)
            if rc != 0:
                print("  中断批量")
                break
        return rc

    if not a.file:
        print("需要 --file(或 --manifest)", file=sys.stderr)
        return 2
    return do_upload(a.profile, a.platform, a.file, a.title, a.desc, a.tags,
                     a.publish, a.headless, a.keep, a.upload_wait, a.claim, a.zone,
                     a.draft, a.private, a.cover)


if __name__ == "__main__":
    raise SystemExit(main())
