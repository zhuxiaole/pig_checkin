import os
import re
import sys
import json
import time
import random
import logging
import functools
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

logger = logging.getLogger("pig_checkin")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
else:
    _handler = logger.handlers[0]

# zendriver 自己的日志默认没有任何 handler（不打印），容器里排查“浏览器起不来”只能靠猜；
# 把它接到本服务的 handler 上，Chromium stderr 与启动失败原因才会真正落到日志里。
_zlogger = logging.getLogger("zendriver")
if _handler not in _zlogger.handlers:
    _zlogger.addHandler(_handler)
    _zlogger.setLevel(logging.INFO)
    _zlogger.propagate = False

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 单次网络请求超时时间 (秒，支持 PIG_REQUEST_TIMEOUT 环境变量覆盖，默认 30 秒)
REQUEST_TIMEOUT = int(os.environ.get("PIG_REQUEST_TIMEOUT", 30))
# 无痕浏览器页面加载超时 (秒，支持 PIG_BROWSER_TIMEOUT 覆盖，默认 35 秒)
BROWSER_TIMEOUT = int(os.environ.get("PIG_BROWSER_TIMEOUT", 35))

def parse_cookies_str(cookie_str: str) -> dict:
    return {k.strip(): v.strip() for item in (cookie_str or "").split(";") if "=" in item for k, v in [item.split("=", 1)]}

def is_cloudflare_challenge(text: str, status_code: int = 200) -> bool:
    if status_code in (403, 503):
        return True
    signals = ["cf-turnstile-wrapper", "cf-turnstile-response", "<title>Just a moment...</title>", "<title>Checking your browser...</title>"]
    return any(sig in text for sig in signals)

# 真正的拦截页特征：正常的签到页正文里也会出现 cf-turnstile-response（表单字段），
# 只看这个字符串会把「已经通过验证的正常页面」误判成还在验证。
_HARD_VERIFY_SIGNALS = (
    "<title>Just a moment...</title>",
    "<title>Checking your browser...</title>",
    "attendance-card--verify",
    "dev8133_cloudflare",
)

def _page_still_verifying(content: str) -> bool:
    return any(sig in content for sig in _HARD_VERIFY_SIGNALS) or "</body" not in content

# Cloudflare 把当前网络判为高风险时，Turnstile 控件会一直转圈、永不下发令牌。
# 这种情况换无头/有头都一样，别再浪费第二次 35 秒。
_CF_REFUSED_MSG = "Cloudflare 人机验证无法自动通过（站点判定当前网络风险过高）；请用浏览器打开该站点过一次验证，之后凭据可复用 24 小时"

# 表单型 Turnstile：令牌解出后必须由页面自身提交（Discuz 插件会校验表单里的 cf-turnstile-response）
# 另外像国语视界这种 dsu_paulsign 必须选一个「签到心情」，默认一个都不勾，服务端会报「心情不正确」
_SUBMIT_TURNSTILE_FORM_JS = """(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    if (!i || !i.value) return false;
    var f = i.form || i.closest('form');
    if (!f) return false;
    var groups = {};
    f.querySelectorAll('input[type=radio]').forEach(function(r){
        (groups[r.name] = groups[r.name] || []).push(r);
    });
    Object.keys(groups).forEach(function(name){
        var g = groups[name];
        if (!g.some(function(r){ return r.checked; })) g[0].checked = true;
    });
    f.submit();
    return true;
})()"""

# 在浏览器内用「页面自己解出的 Turnstile 令牌 + 我们的签到参数」直接 POST，
# 既满足插件对令牌的校验，又能带上 qdxq / todaysay 这类站内要求的字段。
_FETCH_SIGN_FORM_JS = """(async function(payload){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    if (!i || !i.value) return '';
    var f = i.form || i.closest('form');
    if (!f) return '';
    var fd = new FormData(f);
    Object.keys(payload).forEach(function(k){ fd.set(k, payload[k]); });
    fd.set('cf-turnstile-response', i.value);
    var r = await fetch(f.getAttribute('action') || f.action, {method: 'POST', body: fd, credentials: 'include'});
    return await r.text();
})(__PAYLOAD__)"""

_xvfb_lock = threading.Lock()
_browser_lock = threading.Lock()

def ensure_xvfb() -> bool:
    """无 DISPLAY 的 Linux/Docker 环境自动拉起 Xvfb 供有头浏览器渲染；失败则回退无头模式。"""
    import shutil
    import subprocess
    if sys.platform != "linux":
        return bool(os.environ.get("DISPLAY"))
    if os.environ.get("DISPLAY"):
        return True
    if not shutil.which("Xvfb"):
        logger.info("[Xvfb] 系统未安装 Xvfb，回退无头模式")
        return False

    with _xvfb_lock:
        if os.environ.get("DISPLAY"):
            return True
        display = ":99"
        lock_file = "/tmp/.X99-lock"
        sock_path = "/tmp/.X11-unix/X99"
        try:
            # 陈旧锁：进程已死但锁文件残留，会让 Xvfb 拒绝启动、浏览器连上一个不存在的 X server
            if os.path.exists(lock_file) and not os.path.exists(sock_path):
                os.remove(lock_file)
            if not os.path.exists(sock_path):
                subprocess.Popen(
                    ["Xvfb", display, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                for _ in range(50):
                    if os.path.exists(sock_path):
                        break
                    time.sleep(0.1)
            if not os.path.exists(sock_path):
                logger.warning("[Xvfb] 虚拟桌面未能就绪，回退无头模式")
                return False
            os.environ["DISPLAY"] = display
            logger.info("[Xvfb] 虚拟桌面就绪 (%s 1920x1080x24)", display)
            return True
        except Exception as e:
            logger.warning("[Xvfb] 启动虚拟桌面异常，回退无头模式: %s", e)
            return False

def find_browser_executable() -> str:
    env_path = os.environ.get("PIG_BROWSER_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    try:
        import zendriver as zd
        return str(zd.core.config.find_executable())
    except Exception:
        return None

def _can_run_headed() -> bool:
    """能否用有头模式：Windows/macOS 有桌面即可；Linux 需要 Xvfb 虚拟屏。"""
    if sys.platform in ("win32", "darwin"):
        return True
    return ensure_xvfb()


def solve_with_zendriver(url: str, cookies: dict = None, submit_payload: dict = None) -> tuple[bool, str, dict]:
    # 互斥锁：同一时间严格只允许 1 个浏览器实例执行过盾，彻底杜绝并发吃爆系统内存 (OOM)
    with _browser_lock:
        env_headless = os.environ.get("PIG_HEADLESS")
        if env_headless is not None:
            headless = env_headless.lower() not in ("0", "false", "no")
        else:
            headless = not _can_run_headed()

        modes = [headless]
        if headless:
            if _can_run_headed():
                modes.append(False)   # 无头拿不到令牌时，有头再试一次
        else:
            modes.append(True)        # 有头仍失败时，无头再试一次

        result = (False, "", {})
        for i, mode in enumerate(modes):
            if i:
                logger.info("[zendriver] 当前模式过盾失败，改用%s模式重试一次", "无头" if mode else "有头")
            result = _solve_with_zendriver_locked(url, cookies, mode, submit_payload)
            if result[0] or result[1] == _CF_REFUSED_MSG:
                break
        return result


def _solve_with_zendriver_locked(url: str, cookies: dict = None, headless: bool = True,
                                 submit_payload: dict = None) -> tuple[bool, str, dict]:
    import asyncio
    async def _async_solve():
        import zendriver as zd
        import zendriver.cdp.network as network

        has_xvfb = ensure_xvfb()
        exe = find_browser_executable()

        mode_str = "无头浏览器" if headless else ("有头浏览器 (Xvfb虚拟桌面)" if has_xvfb else "有头浏览器")
        logger.info("[zendriver] 启动%s: %s (超时限制: %d 秒)", mode_str, exe or "系统默认Chrome", BROWSER_TIMEOUT)

        browser_args = [
            "--window-size=1920,1080",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
        ]
        if sys.platform == "linux":
            # 容器内以 root 运行必需；缺了这两项 Chromium 会直接起不来（DevTools 端口永不监听）
            browser_args += ["--no-zygote", "--disable-setuid-sandbox"]
        if headless or has_xvfb:
            # 无头 / Xvfb 虚拟屏都没有真实 GPU，不禁用 GPU 加速 Chromium 同样会卡死起不来
            browser_args.append("--disable-gpu")

        browser = None
        try:
            browser = await zd.start(
                browser_executable_path=exe,
                headless=headless,
                sandbox=False,
                lang="zh-CN",
                browser_connection_max_tries=60,
                browser_connection_timeout=0.5,
                browser_args=browser_args
            )
            parsed = urlparse(url)
            domain = parsed.hostname or parsed.netloc or "audiences.me"
            cookie_domain = domain if domain.startswith(".") else f".{domain}"

            # 必须先注入 Cookie 再首次导航：否则站点会先种下 host-only 的同名 Cookie（saltkey/sid），
            # 和注入的 .domain 版本共存，服务端读到错的那个 saltkey，直接判定未登录跳登录页。
            if cookies:
                await browser.cookies.set_all([
                    network.CookieParam(name=k, value=v, domain=cookie_domain, path="/")
                    for k, v in cookies.items()
                ])
            page = await browser.get(url)

            # 部分站点（如多看）首包只回 <head>，body 要再等几秒才流式到达；不等完就当成「还在验证」会白白失败
            for _ in range(12):
                if await page.evaluate("!!document.body && document.readyState !== 'loading'"):
                    break
                await asyncio.sleep(1)

            verify_tried = False
            stuck_since = None
            for _ in range(max(1, BROWSER_TIMEOUT // 2)):
                await asyncio.sleep(2)
                content = await page.get_content()
                if _contains_any(content, ("attendance-card--done", "今天已经签到", "今日已打", "今日已签到")):
                    break
                if not _page_still_verifying(content):
                    break
                # 控件在、页面已加载完、15 秒仍吐不出令牌 = Cloudflare 拒绝，换模式也没用，直接收工
                pending = await page.evaluate("""(function(){
                    var i = document.querySelector('input[name="cf-turnstile-response"]');
                    if (!i || i.value) return 0;
                    return document.readyState === 'complete' ? 1 : 0;
                })()""")
                if pending:
                    stuck_since = stuck_since or time.monotonic()
                    if time.monotonic() - stuck_since >= 15:
                        logger.error("[zendriver] Turnstile 控件一直停在验证中，Cloudflare 拒绝下发令牌")
                        return False, _CF_REFUSED_MSG, {}
                # verify_cf 只对 ShadowRoot 型 CF 挑战有效，对 <div id=turnstile> 这类插件型控件纯等 6 秒
                if not verify_tried:
                    verify_tried = True
                    try:
                        await page.verify_cf(click_delay=2, timeout=6)
                        continue
                    except Exception:
                        pass
                # 点一下实际渲染出来的 Turnstile 复选框（插件型控件要靠这一下才解令牌）
                try:
                    rect = await page.evaluate("""(function(){
                        var fs = document.querySelectorAll('iframe'), best = null;
                        for (var i = 0; i < fs.length; i++) {
                            var r = fs[i].getBoundingClientRect();
                            if (r.width > 60 && r.height > 20 && (!best || r.width * r.height > best.w * best.h))
                                best = {x: r.x, y: r.y, w: r.width, h: r.height};
                        }
                        if (!best) {
                            var box = document.getElementById('turnstile');
                            if (box) { var br = box.getBoundingClientRect();
                                if (br.width > 0) best = {x: br.x, y: br.y, w: br.width, h: Math.max(br.height, 60)}; }
                        }
                        return best;
                    })()""")
                    if rect:
                        await page.mouse_click(rect["x"] + 25, rect["y"] + rect["h"] / 2)
                except Exception:
                    pass
            content = await page.get_content()

            if _page_still_verifying(content):
                logger.error("[zendriver] 超时未通过 Cloudflare / 人机验证")
                return False, "未能通过 Cloudflare / 人机验证", {}

            # 表单型 Turnstile（如国语视界 dsu_paulsign）：控件要 5~10 秒才出令牌，
            # 且令牌必须由页面自身随表单一起提交，否则服务端会以「请先完成人机安全验证」拒收 requests 的 POST。
            # 只认「页面里真有 cf-turnstile-response 输入框」的站点；观众那种把令牌放在 cf-token 的卡片站不进来。
            submitted = False
            has_token_input = await page.evaluate(
                """!!document.querySelector('input[name="cf-turnstile-response"]')""")
            if has_token_input:
                clicked = False
                for _ in range(16):
                    token_len = await page.evaluate("""(function(){
                        var i = document.querySelector('input[name="cf-turnstile-response"]');
                        return i ? i.value.length : -1;
                    })()""")
                    if token_len and token_len > 0:
                        if submit_payload:
                            # 直接在浏览器里 POST：既有页面解出的合法令牌，又能带上站内必填字段
                            try:
                                html = await page.evaluate(
                                    _FETCH_SIGN_FORM_JS.replace("__PAYLOAD__", json.dumps(submit_payload, ensure_ascii=False)),
                                    await_promise=True,
                                )
                            except Exception as e:
                                logger.warning("[zendriver] 浏览器内提交签到表单失败: %s", e)
                                html = ""
                            if html:
                                logger.info("[zendriver] 已在浏览器内用 Turnstile 令牌提交签到表单（%d 字节结果页）", len(html))
                                content = html
                                submitted = True
                        if not submitted:
                            try:
                                submitted = await page.evaluate(_SUBMIT_TURNSTILE_FORM_JS)
                            except Exception:
                                submitted = False
                            if submitted:
                                logger.info("[zendriver] 已用页面自身的 Turnstile 令牌提交签到表单，等待结果页")
                                before_url = page.url
                                for _ in range(10):
                                    await asyncio.sleep(1.2)
                                    content = await page.get_content()
                                    if page.url != before_url or "id=\"qiandao\"" not in content:
                                        break
                        break
                    # 容器/软件渲染环境里 Turnstile 常常退化成需要点一下复选框，
                    # 干等是等不出令牌的；找到那个 iframe 就点一次左侧复选框位置。
                    if not clicked:
                        try:
                            rect = await page.evaluate("""(function(){
                                var fs = document.querySelectorAll('iframe'), best = null;
                                for (var i = 0; i < fs.length; i++) {
                                    var r = fs[i].getBoundingClientRect();
                                    if (r.width > 60 && r.height > 20) {
                                        if (!best || r.width * r.height > best.w * best.h)
                                            best = {x: r.x, y: r.y, w: r.width, h: r.height};
                                    }
                                }
                                return best;
                            })()""")
                            if rect:
                                await page.mouse_click(rect["x"] + rect["w"] * 0.1, rect["y"] + rect["h"] / 2)
                                clicked = True
                                logger.info("[zendriver] 已点击 Turnstile 复选框，等待令牌")
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)

            # 站点要求令牌、但控件始终没解出令牌：等于过盾失败，交给上层换模式重试
            if has_token_input and not submitted:
                logger.error("[zendriver] Turnstile 控件未生成令牌（当前模式下该站点不渲染）")
                return False, "未能通过 Cloudflare / 人机验证", {}

            all_cookies = await browser.cookies.get_all()
            new_cookies = {c.name: c.value for c in all_cookies}
            logger.info("[zendriver] 页面加载完毕，已获取新会话状态及 %d 个 Cookies", len(new_cookies))
            return True, content, new_cookies
        finally:
            if browser is not None:
                try:
                    await browser.stop()
                except Exception:
                    pass
                await asyncio.sleep(0.3)

    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return asyncio.run(_async_solve())
    except Exception as e:
        logger.error("[zendriver] 浏览器执行失败或超时: %s", e)
        return False, f"zendriver 执行失败: {e}", {}


def _persist_site_cookies_merge(site_id: int | None, new_cookies: dict):
    """将过盾获取到的新 Cookies (重点如 cf_clearance) 增量合并持久化到 sites 表。"""
    if not site_id or not new_cookies:
        return
    try:
        from pig_checkin.db import get_db
        from pig_checkin.crypto import encrypt_text, decrypt_text
        with get_db() as conn:
            row = conn.execute("SELECT cookies FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row:
                curr = parse_cookies_str(decrypt_text(row["cookies"] or ""))
                curr.update(new_cookies)
                merged_str = "; ".join(f"{k}={v}" for k, v in curr.items())
                conn.execute("UPDATE sites SET cookies = ? WHERE id = ?", (encrypt_text(merged_str), site_id))
                conn.commit()
                logger.info("💾 [持久化] 站点 ID=%d Cookies 已自动增量更新落库 (更新凭据: %s)", site_id, list(new_cookies.keys()))
    except Exception as e:
        logger.warning("💾 [持久化] 增量更新站点 ID=%d Cookies 异常: %s", site_id, e)


def format_domain(domain: str, default: str = "https://cnlang.org") -> str:
    domain = (domain or "").strip()
    if not domain:
        return default
    if not domain.startswith("http://") and not domain.startswith("https://"):
        domain = "https://" + domain
    return domain.rstrip("/")

def _contains_any(text: str, keywords) -> bool:
    return any(k and k in text for k in keywords)


def _first_group(text: str, patterns) -> str | None:
    """按顺序尝试正则，返回首个命中结果；无捕获组时返回整体匹配。"""
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return (match.group(1) if match.groups() else match.group(0)).strip()
    return None


def _detect_encoding(res) -> str:
    content_type = res.headers.get("Content-Type", "").lower()
    raw = res.content.lower()
    if "gbk" in content_type or b"charset=gbk" in raw or b'charset="gbk"' in raw or b"charset='gbk'" in raw:
        return "gbk"
    if "gb2312" in content_type or b"charset=gb2312" in raw:
        return "gb2312"
    return "utf-8"


def _dig(obj, path: str):
    """按 a.b.c 取嵌套字典字段，缺失返回 None。"""
    return functools.reduce(lambda d, k: d.get(k) if isinstance(d, dict) else None, path.split("."), obj)


def _discuz_message_result(text: str, site_label: str, user_name: str, tpl: dict) -> tuple[bool, str]:
    """按 Discuz showmessage 页/提示块判定签到结果。"""
    soup = BeautifulSoup(text, "html.parser")
    msg = ""
    for tag, name in tpl["message_selectors"]:
        node = soup.find(tag, {"class": name}) or soup.find(tag, {"id": name})
        if node and node.text.strip():
            msg = node.text.strip()
            break
    msg = msg or text[:120].strip() or "签到提交完成"
    # Discuz 的 showmessage 把「请稍候，本站将自动跳转…」塞在同一个节点里，只留第一行
    msg = msg.splitlines()[0].strip() or msg

    if _contains_any(text, tpl["success_keywords"]):
        logger.info("[%s] 用户 [%s] 签到成功: %s", site_label, user_name, msg)
        return True, f"用户 [{user_name}] 签到成功: {msg}"
    if _contains_any(text, tpl["already_signed_keywords"]):
        logger.info("[%s] 用户 [%s] 今日已签到过: %s", site_label, user_name, msg)
        return True, f"用户 [{user_name}] 今日已签到过，无需重复签到"
    logger.warning("[%s] 签到返回提示: %s", site_label, msg)
    return False, f"签到返回提示: {msg}"


def checkin_discuz_dsu_paulsign(site_dict: dict, tpl: dict) -> tuple[bool, str]:
    site_label = tpl["name"]
    domain = format_domain(site_dict.get("domain"), default=tpl["default_domain"])
    cookies = parse_cookies_str(site_dict.get("cookies", ""))
    
    sign_page_url = f"{domain}{tpl['sign_path']}"
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": sign_page_url,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Upgrade-Insecure-Requests": "1"
    }

    session = requests.Session()
    session.headers.update(headers)
    session.cookies.update(cookies)

    logger.info("[%s] 发起 GET 访问签到页面: %s", site_label, sign_page_url)
    try:
        res = session.get(sign_page_url, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout:
        logger.error("[%s] 访问签到页面超时 (超过 %d 秒): %s", site_label, REQUEST_TIMEOUT, sign_page_url)
        return False, f"网络请求超时 (超过 {REQUEST_TIMEOUT} 秒)"
    except Exception as e:
        logger.error("[%s] 访问签到页面网络失败: %s", site_label, e)
        return False, f"网络请求失败: {e}"

    encoding = tpl["encoding"] or _detect_encoding(res)
    try:
        res_text = res.content.decode(encoding, errors="replace")
    except Exception:
        res_text = res.text

    # 过盾前先从页面里取 formhash / 心情，浏览器内提交时要把这些字段一起带上
    pre_soup = BeautifulSoup(res_text, "html.parser")
    pre_formhash = _first_group(res_text, tpl["formhash_patterns"])
    if not pre_formhash:
        pre_input = pre_soup.find("input", {"name": tpl["formhash_field"]})
        pre_formhash = pre_input.get("value") if pre_input else None
    pre_payload = None
    if pre_formhash:
        pre_payload = {**tpl["submit_body"], tpl["formhash_field"]: pre_formhash}
        if tpl["greeting_field"]:
            pre_payload[tpl["greeting_field"]] = random.choice(tpl["greetings"] or [""]).replace("{site}", site_label)

    if is_cloudflare_challenge(res_text, res.status_code) or _contains_any(res_text, tpl["gate_keywords"]):
        logger.warning("[%s] 检测到 Cloudflare 防护或人机验证拦截 (HTTP %d)，启动浏览器过盾处理...", site_label, res.status_code)
        ok, content, new_cookies = solve_with_zendriver(sign_page_url, cookies, submit_payload=pre_payload)
        if not ok:
            logger.error("[%s] 浏览器绕过 Cloudflare 失败: %s", site_label, content)
            if content == _CF_REFUSED_MSG:
                return False, content
            return False, f"检测到 Cloudflare 防护，且浏览器绕过失败: {content}"
        logger.info("[%s] 浏览器成功通过 Cloudflare 质询，已更新会话凭据", site_label)
        session.cookies.update(new_cookies)
        _persist_site_cookies_merge(site_dict.get("id"), new_cookies)
        # 浏览器里已经带着 Turnstile 令牌把签到表单提交掉了，content 就是结果页（此时页面已无签到表单）
        soup_c = BeautifulSoup(content, "html.parser")
        has_sign_form = any(soup_c.find(tag, {"name": name}) is not None for tag, name in tpl["sign_form_selectors"])
        has_message = any(soup_c.find(tag, {"class": name}) or soup_c.find(tag, {"id": name})
                          for tag, name in tpl["message_selectors"])
        if not has_sign_form and (has_message or _contains_any(content, tpl["success_keywords"])
                                  or _contains_any(content, tpl["already_signed_keywords"])):
            return _discuz_message_result(content, site_label, site_dict.get("name", "用户"), tpl)
        # 否则用更新后的会话重新 GET，加载真正的签到主页
        try:
            res_refreshed = session.get(sign_page_url, timeout=REQUEST_TIMEOUT)
            res_text = res_refreshed.content.decode(encoding, errors="replace")
        except Exception:
            res_text = content

    # 登录状态判定：UID 或退出链接存在，且无未登录提示
    if _contains_any(res_text, tpl["gate_keywords"]):
        logger.warning("[%s] 命中站点人机验证闸门 (未通过或已过期)", site_label)
        return False, tpl["gate_message"]

    discuz_uid = _first_group(res_text, tpl["logged_in_patterns"])
    is_logged_in = bool(discuz_uid and discuz_uid.isdigit() and int(discuz_uid) > 0) or _contains_any(res_text, tpl["logged_in_keywords"])
    if not is_logged_in or _contains_any(res_text, tpl["need_login_keywords"]):
        logger.warning("[%s] 登录凭据无效或已过期 (uid=%s)", site_label, discuz_uid)
        return False, "Cookies 已失效或未登录，请更新 Cookies"

    user_name = _first_group(res_text, tpl["user_patterns"]) or site_dict.get("name", "用户")

    soup = BeautifulSoup(res_text, "html.parser")
    has_sign_form = any(soup.find(tag, {"name": name}) is not None for tag, name in tpl["sign_form_selectors"])

    # 只有当签到表单不存在时，才判定为今日已签到（严禁使用“今日已签”裸字，否则会误命中侧边栏的“xxx人今日已签到”全站统计数据）
    if not has_sign_form and _contains_any(res_text, tpl["already_signed_page_keywords"]):
        logger.info("[%s] 用户 [%s] 今日已完成签到 (未检测到待提交表单)，无需重复签到", site_label, user_name)
        return True, f"用户 [{user_name}] 今日已签到过，无需重复签到"

    # 先按模板正则取（有些站点页面里还有个登录表单的 formhash 会干扰 input 兜底）
    formhash = _first_group(res_text, tpl["formhash_patterns"])
    if not formhash:
        formhash_input = soup.find("input", {"name": tpl["formhash_field"]})
        formhash = formhash_input.get("value") if formhash_input else None
    if not formhash:
        logger.error("[%s] 未能从页面中提取到 formhash", site_label)
        return False, "未能提取到 formhash"

    todaysay = random.choice(tpl["greetings"] or [""]).replace("{site}", site_label)

    # 部分插件的令牌走 URL 而不是表单字段，submit_path 里可用 {formhash} 占位
    post_url = f"{domain}{tpl['submit_path']}".replace("{formhash}", formhash)
    payload = {**tpl["submit_body"], tpl["formhash_field"]: formhash}
    if tpl["greeting_field"]:
        payload[tpl["greeting_field"]] = todaysay

    logger.info("[%s] 提交签到表单 POST: %s (formhash: %s%s)", site_label, post_url, formhash,
                f", {tpl['greeting_field']}: {todaysay}" if tpl["greeting_field"] else "")
    try:
        if encoding in ("gbk", "gb2312"):
            import urllib.parse
            data_encoded = urllib.parse.urlencode(payload, encoding=encoding).encode("ascii")
            sign_res = session.post(
                post_url,
                data=data_encoded,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=REQUEST_TIMEOUT
            )
        else:
            sign_res = session.post(post_url, data=payload, timeout=REQUEST_TIMEOUT)

        try:
            sign_text = sign_res.content.decode(encoding, errors="replace")
        except Exception:
            sign_text = sign_res.text

        # 部分 Discuz 插件（如 erling_qd）的签到按钮走 AJAX，返回 JSON 而非 HTML 提示
        try:
            sign_json = sign_res.json()
        except Exception:
            sign_json = None
        if isinstance(sign_json, dict) and ("success" in sign_json or "message" in sign_json):
            msg = str(sign_json.get("message") or "签到提交完成")
            if sign_json.get("success"):
                logger.info("[%s] 用户 [%s] 签到成功: %s", site_label, user_name, msg)
                return True, f"用户 [{user_name}] 签到成功: {msg}"
            logger.warning("[%s] 签到接口返回失败: %s", site_label, msg)
            return False, f"签到失败: {msg}"

        return _discuz_message_result(sign_text, site_label, user_name, tpl)
    except requests.exceptions.Timeout:
        logger.error("[%s] 提交签到表单超时 (超过 %d 秒)", site_label, REQUEST_TIMEOUT)
        return False, f"签到提交超时 (超过 {REQUEST_TIMEOUT} 秒)"
    except Exception as e:
        logger.error("[%s] 提交签到请求失败: %s", site_label, e)
        return False, f"签到请求提交失败: {e}"

def checkin_yysub(site_dict: dict, tpl: dict) -> tuple[bool, str]:
    domain = format_domain(site_dict.get("domain"), default=tpl["default_domain"])
    username = (site_dict.get("username") or "").strip()
    password = (site_dict.get("password") or "").strip()
    cookie_str = (site_dict.get("cookies") or "").strip()

    if not username and not cookie_str:
        return False, "未配置账号/密码或 Cookies 凭据"

    session = requests.Session()
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"{domain}/user/login",
        "Origin": domain,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    session.headers.update(headers)

    if cookie_str:
        session.cookies.update(parse_cookies_str(cookie_str))

    site_name = site_dict.get("name", tpl["name"])

    if username and password:
        login_url = f"{domain}{tpl['login_path']}"
        login_data = {
            key: str(value).replace("{username}", username).replace("{password}", password)
            for key, value in tpl["login_body"].items()
        }
        logger.info("[%s] 发起 POST 用户登录: %s (账号: %s)", site_name, login_url, username)
        try:
            res_login = session.post(login_url, data=login_data, timeout=REQUEST_TIMEOUT)
            try:
                login_json = res_login.json()
            except Exception:
                login_json = {}
            if login_json and login_json.get("status") in tpl["login_fail_status"]:
                err_msg = login_json.get("info") or "账号或密码错误"
                logger.error("[%s] 登录失败: %s", site_name, err_msg)
                return False, f"登录失败: {err_msg}"
        except requests.exceptions.Timeout:
            logger.error("[%s] 登录请求超时 (超过 %d 秒)", site_name, REQUEST_TIMEOUT)
            return False, f"登录请求超时 (超过 {REQUEST_TIMEOUT} 秒)"
        except Exception as e:
            logger.warning("[%s] 登录请求异常: %s", site_name, e)

    info_url = f"{domain}{tpl['info_path']}"
    logger.info("[%s] 访问用户信息接口: %s", site_name, info_url)
    try:
        res_info = session.get(info_url, timeout=REQUEST_TIMEOUT)
        try:
            info_json = res_info.json()
        except Exception:
            info_json = {}

        status = info_json.get("status")
        if status in tpl["unauth_status"]:
            return False, "未登录或登录会话已失效 (status: 4001)"

        data = info_json.get("data") if isinstance(info_json.get("data"), dict) else {}
        usercount = data.get("usercount") if isinstance(data.get("usercount"), dict) else {}
        userinfo = data.get("userinfo") if isinstance(data.get("userinfo"), dict) else {}

        cont_login = usercount.get("cont_login")
        point = userinfo.get("point")
        nickname = userinfo.get("nickname") or username or "用户"

        if any(_dig(info_json, field) is not None for field in tpl["success_fields"]):
            msg = f"登录成功，用户 [{nickname}] 连续登录: {cont_login if cont_login is not None else '未知'} 天，人人钻: {point if point is not None else '未知'}"
            logger.info("[%s] %s", site_name, msg)
            return True, msg
        elif status in tpl["ok_status"] or "uid" in str(data):
            msg = f"登录成功 (返回状态: {status})"
            logger.info("[%s] %s", site_name, msg)
            return True, msg
        else:
            msg = info_json.get("info") or f"未知状态码: {status}"
            return False, f"获取用户信息失败: {msg}"
    except requests.exceptions.Timeout:
        logger.error("[%s] 请求用户信息接口超时", site_name)
        return False, f"请求用户信息超时 (超过 {REQUEST_TIMEOUT} 秒)"
    except Exception as e:
        logger.error("[%s] 请求用户信息接口异常: %s", site_name, e)
        return False, f"请求用户信息接口异常: {e}"

def checkin_nexusphp(site_dict: dict, tpl: dict) -> tuple[bool, str]:
    site_label = tpl["name"]
    domain = format_domain(site_dict.get("domain"), default=tpl["default_domain"])
    cookies = parse_cookies_str(site_dict.get("cookies", ""))
    
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"{domain}/index.php",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Upgrade-Insecure-Requests": "1"
    }

    session = requests.Session()
    session.headers.update(headers)
    session.cookies.update(cookies)

    attendance_url = f"{domain}{tpl['sign_path']}"
    site_name = site_dict.get("name", site_label)

    logger.info("[%s] 发起 GET 访问 NexusPHP 签到页: %s", site_name, attendance_url)
    try:
        res = session.get(attendance_url, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout:
        logger.error("[%s] 访问签到页超时 (超过 %d 秒): %s", site_name, REQUEST_TIMEOUT, attendance_url)
        return False, f"网络请求超时 (超过 {REQUEST_TIMEOUT} 秒)"
    except Exception as e:
        logger.error("[%s] 访问签到页网络失败: %s", site_name, e)
        return False, f"网络请求失败: {e}"

    if is_cloudflare_challenge(res.text, res.status_code) or _contains_any(res.text, tpl["verify_keywords"]):
        logger.warning("[%s] 检测到 Cloudflare / 人机验证拦截 (HTTP %d)，启动浏览器过盾处理...", site_name, res.status_code)
        ok, content, new_cookies = solve_with_zendriver(attendance_url, cookies)
        if not ok:
            logger.error("[%s] 浏览器绕过失败: %s", site_name, content)
            if content == _CF_REFUSED_MSG:
                return False, content
            return False, f"检测到 Cloudflare 防护，且浏览器绕过失败: {content}"
        logger.info("[%s] 浏览器成功通过 Cloudflare 质询，已更新会话凭据", site_name)
        session.cookies.update(new_cookies)
        _persist_site_cookies_merge(site_dict.get("id"), new_cookies)
        if "attendance-card--done" in content or "今天已经签到" in content:
            res_text = content
        else:
            try:
                res_refreshed = session.get(attendance_url, timeout=REQUEST_TIMEOUT)
                res_text = res_refreshed.text
            except Exception:
                res_text = content
    else:
        res_text = res.text

    # 检查是否登录（NexusPHP 登录失效判定）
    if _contains_any(res_text, tpl["login_fail_keywords"]) or tpl["login_required_keyword"] not in res_text:
        logger.warning("[%s] 登录凭据无效或已失效", site_name)
        return False, "Cookies 已失效或未登录（请检查是否包含 c_secure_uid/pass 及 cf_clearance）"

    user_name = _first_group(res_text, tpl["user_patterns"]) or "NexusPT用户"

    # 判定签到状态：已签到 > 仍需人机验证 > 本次签到成功
    if _contains_any(res_text, tpl["done_keywords"]):
        attended_bonus = _first_group(res_text, tpl["bonus_patterns"]) or ""
        card_desc = _first_group(res_text, tpl["card_desc_patterns"]) or ""
        award_desc = _first_group(res_text, tpl["award_patterns"]) or ""
        info = f"今日已签到 (获得 {attended_bonus} 粒爆米花)" if attended_bonus else (card_desc or award_desc or "今日已签到")
        logger.info("[%s] 用户 [%s] %s", site_name, user_name, info)
        return True, f"用户 [{user_name}] {info}"

    if _contains_any(res_text, tpl["verify_keywords"]):
        logger.warning("[%s] 签到页面需完成 Turnstile 人机验证", site_name)
        return False, "需完成人机验证 (Turnstile)，未完成签到"

    if _contains_any(res_text, tpl["success_keywords"]):
        award_info = _first_group(res_text, tpl["award_patterns"]) or "签到成功"
        logger.info("[%s] 用户 [%s] 签到成功: %s", site_name, user_name, award_info)
        return True, f"用户 [{user_name}] {award_info}"

    # 当天领取入口用完就不渲染的站点（如 BTSchool）：页面已无入口 = 今日已领过，
    # 放在成功判定之后，免得刚领取那一次也被吞成「已领取」而丢掉奖励数额。
    if tpl["already_signed_if_absent"] and not _contains_any(res_text, tpl["already_signed_if_absent"]):
        logger.info("[%s] 用户 [%s] 今日已签到 (页面已无领取入口)", site_name, user_name)
        return True, f"用户 [{user_name}] 今日已签到，无需重复签到"

    # 未能识别成功特征，避免误报为成功
    soup = BeautifulSoup(res_text, "html.parser")
    title_text = soup.title.string.strip() if soup.title else ""
    logger.warning("[%s] 未能匹配到明确的签到成功特征 (页面标题: %s)", site_name, title_text)
    return False, f"未匹配到明确的签到成功特征 (页面标题: {title_text})"

def checkin_peergo(site_dict: dict, tpl: dict) -> tuple[bool, str]:
    """PeerGo 系站点：用个人 API Key 调它的自动化接口签到。

    接口契约（见 PeerGo 的 moviepilot 兼容层）：认证头 `Authorization: Bearer <key>`，
    成功返回 {"code":0,...,"data":{reward,total_days,current_streak,...}}，
    当天已签到时返回 {"code":1,"message":"今天已签到"}，密钥失效 401、缺少 attendance:claim 权限 403。
    """
    site_name = tpl["name"]
    domain = format_domain(site_dict.get("domain"), default=tpl["default_domain"])
    api_key = (site_dict.get("token") or "").strip()
    if not api_key:
        logger.error("[%s] 未配置密钥", site_name)
        return False, "未配置密钥 (个人 API Key)"

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    session = requests.Session()
    session.headers.update(headers)

    url = f"{domain}{tpl['claim_path']}"
    body = dict(tpl["claim_body"])
    if tpl["claim_mode"]:
        body["mode"] = tpl["claim_mode"]

    logger.info("[%s] POST 签到接口: %s", site_name, url)
    try:
        res = session.post(url, json=body, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout:
        logger.error("[%s] 签到接口超时 (超过 %d 秒)", site_name, REQUEST_TIMEOUT)
        return False, f"网络请求超时 (超过 {REQUEST_TIMEOUT} 秒)"
    except Exception as e:
        logger.error("[%s] 签到接口请求失败: %s", site_name, e)
        return False, f"网络请求失败: {e}"

    try:
        payload = res.json()
    except Exception:
        logger.error("[%s] 签到接口返回非 JSON (HTTP %d): %s", site_name, res.status_code, res.text[:200])
        return False, f"接口返回异常 (HTTP {res.status_code})"
    if not isinstance(payload, dict):
        return False, f"接口返回异常 (HTTP {res.status_code})"

    code = payload.get("code", res.status_code)
    message = str(payload.get("message") or "").strip()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    if code in tpl["unauth_codes"]:
        logger.error("[%s] 密钥无效: %s", site_name, message)
        return False, f"密钥无效或已撤销 ({message})"
    if code in tpl["forbidden_codes"]:
        logger.error("[%s] 密钥权限不足: %s", site_name, message)
        return False, f"密钥权限不足，缺少 attendance:claim 权限 ({message})"
    if code in tpl["already_claimed_codes"]:
        logger.info("[%s] 今日已签到: %s", site_name, message or "今天已签到")
        return True, f"今日已签到，无需重复签到 ({message or '今天已签到'})"
    if code != 0:
        logger.warning("[%s] 签到接口返回失败 code=%s: %s", site_name, code, message)
        return False, f"签到失败: {message or code}"

    reward = data.get("reward")
    streak = data.get("current_streak")
    total = data.get("total_days")
    parts = [f"获得 {reward} 魔力" if reward is not None else "签到成功"]
    if streak is not None:
        parts.append(f"连续 {streak} 天")
    if total is not None:
        parts.append(f"累计 {total} 天")
    info = "，".join(parts)
    logger.info("[%s] 签到成功: %s", site_name, info)
    return True, info

# 协议实现注册表，站点通过模板的 flow 字段选用（见 templates.py）
FLOWS = {
    "discuz": checkin_discuz_dsu_paulsign,
    "nexusphp": checkin_nexusphp,
    "yysub": checkin_yysub,
    "peergo": checkin_peergo,
}

def execute_checkin_once(site_dict: dict) -> tuple[bool, str]:
    from pig_checkin.crypto import decrypt_text
    from pig_checkin.templates import get_template
    site_dict = dict(site_dict)
    for field in ("cookies", "username", "password", "token"):
        if site_dict.get(field):
            site_dict[field] = decrypt_text(site_dict[field])

    site_type = site_dict.get("site_type", "cnlang")
    tpl = get_template(site_type)
    func = FLOWS.get(tpl["flow"]) if tpl else None
    if not func:
        return False, f"不支持的站点类型: {site_type}"
    return func(site_dict, tpl)

def execute_checkin(site_dict: dict) -> tuple[bool, str]:
    from pig_checkin.db import get_db
    import datetime

    site_id = site_dict.get("id")
    site_name = site_dict.get("name", "未命名站点")
    site_type = site_dict.get("site_type", "unknown")
    domain = site_dict.get("domain", "")
    max_retries = max(0, int(site_dict.get("max_retries", 3) or 0))
    retry_interval = max(1, int(site_dict.get("retry_interval", 60) or 1))

    logger.info("========== [签到启动] %s (类型: %s, 域名: %s, 重试配置: %d次/%d秒) ==========", site_name, site_type, domain, max_retries, retry_interval)

    last_msg = ""
    for attempt in range(max_retries + 1):
        if attempt > 0:
            logger.info("[%s] 开始第 %d/%d 次重试...", site_name, attempt, max_retries)
        else:
            logger.info("[%s] 发起首次签到请求...", site_name)

        ok, msg = execute_checkin_once(site_dict)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if ok:
            full_msg = f"{msg} (第 {attempt} 次重试成功)" if attempt > 0 else msg
            logger.info("========== [签到成功] %s: %s ==========", site_name, full_msg)
            if site_id:
                from pig_checkin.scheduler import compute_next_run
                next_run_str = compute_next_run(site_dict.get("schedule_type", "random"), site_dict.get("schedule_time", "09:00"), next_day=True)
                with get_db() as conn:
                    conn.execute(
                        "UPDATE sites SET last_status = '签到成功', last_run = ?, next_run = ? WHERE id = ?",
                        (now_str, next_run_str, site_id)
                    )
                    conn.execute(
                        "INSERT INTO logs (site_id, site_name, status, message, created_at) VALUES (?, ?, '成功', ?, ?)",
                        (site_id, site_name, full_msg, now_str)
                    )
                    conn.commit()
            return True, full_msg

        last_msg = msg
        logger.warning("[%s] 签到尝试未成功: %s", site_name, msg)

        # 每次失败，立即写入 web 日志，若仍有重试机会则状态标为“失败重试”
        with get_db() as conn:
            if attempt < max_retries:
                retry_status = "失败重试"
                if site_id:
                    conn.execute("UPDATE sites SET last_status = ?, last_run = ? WHERE id = ?", (retry_status, now_str, site_id))
                log_msg = f"第 {attempt + 1} 次尝试未成功: {msg} (等待 {retry_interval} 秒后重试)"
                conn.execute(
                    "INSERT INTO logs (site_id, site_name, status, message, created_at) VALUES (?, ?, '失败', ?, ?)",
                    (site_id, site_name, log_msg, now_str)
                )
            else:
                final_status = "签到失败"
                next_run_str = ""
                if site_id:
                    from pig_checkin.scheduler import compute_failure_next_run
                    next_run_str = compute_failure_next_run(site_dict.get("schedule_type", "random"), site_dict.get("schedule_time", "09:00"))
                    conn.execute("UPDATE sites SET last_status = ?, last_run = ?, next_run = ? WHERE id = ?", (final_status, now_str, next_run_str, site_id))
                log_msg = f"尝试 {max_retries + 1} 次均失败，最终错误: {msg} (已推迟至 {next_run_str} 重新尝试)" if next_run_str else f"尝试 {max_retries + 1} 次均失败，最终错误: {msg}"
                conn.execute(
                    "INSERT INTO logs (site_id, site_name, status, message, created_at) VALUES (?, ?, '失败', ?, ?)",
                    (site_id, site_name, log_msg, now_str)
                )
            conn.commit()

        if attempt < max_retries:
            logger.info("[%s] 等待 %d 秒后进行第 %d 次尝试...", site_name, retry_interval, attempt + 1)
            time.sleep(retry_interval)

    final_msg = f"尝试 {max_retries + 1} 次均失败，最终错误: {last_msg}"
    logger.error("========== [签到结束-失败] %s: %s ==========", site_name, final_msg)
    return False, final_msg
