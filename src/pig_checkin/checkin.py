import os
import re
import time
import random
import logging
import functools
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

logger = logging.getLogger("pig_checkin")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

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

def solve_with_cloakbrowser(url: str, cookies: dict = None) -> tuple[bool, str, dict]:
    """使用 CloakBrowser (底层带 C++ 级指纹伪装的 Stealth Chromium) 加载页面并通过 Cloudflare 验证。"""
    try:
        import urllib.parse
        from cloakbrowser import launch

        if os.environ.get("PIG_BROWSER_PATH"):
            os.environ.setdefault("CLOAKBROWSER_BINARY_PATH", os.environ["PIG_BROWSER_PATH"])

        headless = os.environ.get("PIG_HEADLESS", "true").lower() not in ("0", "false", "no")
        mode_str = "无头浏览器" if headless else "有头浏览器"
        logger.info("[CloakBrowser] 启动%s (超时限制: %d 秒)", mode_str, BROWSER_TIMEOUT)

        browser = launch(
            headless=headless,
            humanize=True,
            args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-setuid-sandbox"] if headless else []
        )
        try:
            context = browser.new_context()
            parsed = urllib.parse.urlparse(url)
            domain = parsed.hostname or parsed.netloc or "audiences.me"
            cookie_domain = domain if domain.startswith(".") else f".{domain}"
            if cookies:
                context.add_cookies([
                    {"name": k, "value": v, "domain": cookie_domain, "path": "/"}
                    for k, v in cookies.items()
                ])

            page = context.new_page()
            try:
                page.goto(url, timeout=BROWSER_TIMEOUT * 1000)
            except Exception as e:
                logger.warning("[CloakBrowser] 页面加载等待异常或部分超时: %s", e)

            content = page.content()
            for _ in range(max(1, BROWSER_TIMEOUT // 2)):
                time.sleep(2)
                content = page.content()
                if "attendance-card--done" in content or "今天已经签到" in content:
                    break
                if is_cloudflare_challenge(content) or "attendance-card--verify" in content:
                    try:
                        cf_frame = page.frame(url=lambda u: "challenges.cloudflare.com" in u)
                        if cf_frame:
                            chk = cf_frame.locator("input[type=checkbox], #challenge-stage")
                            if chk.is_visible(timeout=1000):
                                chk.click()
                    except Exception:
                        pass
                elif "</body" in content:
                    break

            if is_cloudflare_challenge(content) or "attendance-card--verify" in content:
                logger.error("[CloakBrowser] 超时未通过 Cloudflare / 人机验证")
                return False, "未能通过 Cloudflare / 人机验证", {}

            all_cookies = context.cookies()
            new_cookies = {c["name"]: c["value"] for c in all_cookies}
            logger.info("[CloakBrowser] 页面加载完毕，已获取新会话状态及 %d 个 Cookies", len(new_cookies))
            return True, content, new_cookies
        finally:
            browser.close()
    except Exception as e:
        logger.error("[CloakBrowser] 执行失败或超时: %s", e)
        return False, f"CloakBrowser 执行失败: {e}", {}

solve_with_zendriver = solve_with_cloakbrowser

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

    if is_cloudflare_challenge(res_text, res.status_code):
        logger.warning("[%s] 检测到 Cloudflare 防护拦截 (HTTP %d)，启动无头浏览器处理...", site_label, res.status_code)
        ok, content, new_cookies = solve_with_cloakbrowser(sign_page_url, cookies)
        if not ok:
            logger.error("[%s] 无头浏览器绕过 Cloudflare 失败: %s", site_label, content)
            return False, f"检测到 Cloudflare 防护，且无头浏览器绕过失败: {content}"
        logger.info("[%s] 无头浏览器成功通过 Cloudflare 质询，已更新会话凭据", site_label)
        session.cookies.update(new_cookies)
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

        sign_soup = BeautifulSoup(sign_text, "html.parser")
        msg = ""
        for tag, name in tpl["message_selectors"]:
            node = sign_soup.find(tag, {"class": name}) or sign_soup.find(tag, {"id": name})
            if node and node.text.strip():
                msg = node.text.strip()
                break
        msg = msg or sign_text[:120].strip() or "签到提交完成"
        # Discuz 的 showmessage 把「请稍候，本站将自动跳转…」塞在同一个节点里，只留第一行
        msg = msg.splitlines()[0].strip() or msg

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

        if _contains_any(sign_text, tpl["success_keywords"]):
            logger.info("[%s] 用户 [%s] 签到成功: %s", site_label, user_name, msg)
            return True, f"用户 [{user_name}] 签到成功: {msg}"
        if _contains_any(sign_text, tpl["already_signed_keywords"]):
            logger.info("[%s] 用户 [%s] 今日已签到过: %s", site_label, user_name, msg)
            return True, f"用户 [{user_name}] 今日已签到过，无需重复签到"
        logger.warning("[%s] 签到返回提示: %s", site_label, msg)
        return False, f"签到返回提示: {msg}"
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
        logger.warning("[%s] 检测到 Cloudflare / 人机验证拦截 (HTTP %d)，启动无头浏览器处理...", site_name, res.status_code)
        ok, content, new_cookies = solve_with_cloakbrowser(attendance_url, cookies)
        if not ok:
            logger.error("[%s] 无头浏览器绕过失败: %s", site_name, content)
            return False, f"检测到 Cloudflare 防护，且无头浏览器绕过失败: {content}"
        logger.info("[%s] 无头浏览器成功通过 Cloudflare 质询，已更新会话凭据", site_name)
        session.cookies.update(new_cookies)
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
                if site_id:
                    conn.execute("UPDATE sites SET last_status = ?, last_run = ? WHERE id = ?", (final_status, now_str, site_id))
                log_msg = f"尝试 {max_retries + 1} 次均失败，最终错误: {msg}"
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
