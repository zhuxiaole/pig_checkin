import os
import re
import time
import random
import logging
import requests
from bs4 import BeautifulSoup
from typing import Tuple
from urllib.parse import urlparse

os.environ.setdefault("INVISIBLE_PLAYWRIGHT_CACHE_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".cache", "inv_pw")))

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
# 无痕浏览器页面加载超时 (毫秒，支持 PIG_PLAYWRIGHT_TIMEOUT 覆盖，默认 35000 毫秒)
PLAYWRIGHT_TIMEOUT = int(os.environ.get("PIG_PLAYWRIGHT_TIMEOUT", 35000))

def parse_cookies_str(cookie_str: str) -> dict:
    cookies = {}
    if not cookie_str:
        return cookies
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies

def is_cloudflare_challenge(text: str, status_code: int = 200) -> bool:
    if status_code in (403, 503):
        return True
    signals = ["cf-turnstile-wrapper", "cf-turnstile-response", "<title>Just a moment...</title>", "<title>Checking your browser...</title>"]
    return any(sig in text for sig in signals)

def solve_with_invisible_playwright(url: str, cookies: dict = None) -> Tuple[bool, str, dict]:
    try:
        from invisible_playwright import InvisiblePlaywright
        logger.info("[Playwright] 启动无痕浏览器访问页面: %s (超时限制: %d 秒)", url, PLAYWRIGHT_TIMEOUT // 1000)
        with InvisiblePlaywright(headless=True) as pw:
            page = pw.new_page()
            if cookies:
                pw_cookies = [{"name": k, "value": v, "url": url} for k, v in cookies.items()]
                try:
                    page.context.add_cookies(pw_cookies)
                except Exception:
                    pass
            page.goto(url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT)
            page.wait_for_timeout(3000)
            content = page.content()
            new_cookies = {c["name"]: c["value"] for c in page.context.cookies()}
            logger.info("[Playwright] 页面渲染完毕，已获取新会话状态及 %d 个 Cookies", len(new_cookies))
            return True, content, new_cookies
    except Exception as e:
        logger.error("[Playwright] 无头浏览器执行失败或超时: %s", e)
        return False, f"invisible_playwright 执行失败: {e}", {}

def format_domain(domain: str, default: str = "https://cnlang.org") -> str:
    domain = (domain or "").strip()
    if not domain:
        return default
    if not domain.startswith("http://") and not domain.startswith("https://"):
        domain = "https://" + domain
    return domain.rstrip("/")

def checkin_discuz_dsu_paulsign(
    site_dict: dict,
    default_domain: str,
    sign_path: str = "/plugin.php?id=dsu_paulsign:sign",
    site_label: str = "论坛"
) -> Tuple[bool, str]:
    domain = format_domain(site_dict.get("domain"), default=default_domain)
    cookie_str = site_dict.get("cookies", "")
    cookies = parse_cookies_str(cookie_str)
    
    sign_page_url = f"{domain}{sign_path}"
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

    # 自动识别并解码 GBK / GB2312 编码
    encoding = "utf-8"
    raw_content = res.content
    content_type = res.headers.get("Content-Type", "").lower()
    if "gbk" in content_type or b"charset=gbk" in raw_content.lower() or b"charset=\"gbk\"" in raw_content.lower() or b"charset='gbk'" in raw_content.lower():
        encoding = "gbk"
    elif "gb2312" in content_type or b"charset=gb2312" in raw_content.lower():
        encoding = "gb2312"

    try:
        res_text = raw_content.decode(encoding, errors="replace")
    except Exception:
        res_text = res.text

    if is_cloudflare_challenge(res_text, res.status_code):
        logger.warning("[%s] 检测到 Cloudflare 防护拦截 (HTTP %d)，启动无头浏览器处理...", site_label, res.status_code)
        ok, content, new_cookies = solve_with_invisible_playwright(sign_page_url, cookies)
        if not ok:
            logger.error("[%s] 无头浏览器绕过 Cloudflare 失败: %s", site_label, content)
            return False, f"检测到 Cloudflare 防护，且无头浏览器绕过失败: {content}"
        logger.info("[%s] 无头浏览器成功通过 Cloudflare 质询，已更新会话凭据", site_label)
        session.cookies.update(new_cookies)
        res_text = content

    # 统一精准的 Discuz 登录状态判定 (利用全局 UID、退出链接与未登录提示)
    uid_match = re.search(r"discuz_uid\s*=\s*['\"]?(\d+)['\"]?", res_text)
    discuz_uid = int(uid_match.group(1)) if uid_match else None
    has_logout = "action=logout" in res_text

    is_logged_in = (discuz_uid and discuz_uid > 0) or has_logout
    if not is_logged_in or "您需要先登录才能继续本操作" in res_text or "<title>登录" in res_text:
        logger.warning("[%s] 登录凭据无效或已过期 (uid=%s, has_logout=%s)", site_label, discuz_uid, has_logout)
        return False, "Cookies 已失效或未登录，请更新 Cookies"

    # 多模板通用提取用户名
    user_match = (
        re.search(r'title="访问我的空间">(.*?)</a>', res_text) or
        re.search(r'class="vwmy"><a [^>]*>(.*?)</a>', res_text) or
        re.search(r'<a [^>]*space-uid-\d+\.html[^>]*title="([^"]+)"', res_text) or
        re.search(r'<span class="user_avt">([^<]+)</span>', res_text)
    )
    user_name = user_match.group(1).strip() if user_match else site_dict.get("name", "用户")

    if (
        "今日已签" in res_text or
        "您今天已经签到过了" in res_text or
        "您今日已经签到" in res_text or
        "已经签到过了" in res_text
    ):
        logger.info("[%s] 用户 [%s] 今日已完成签到，无需重复提交", site_label, user_name)
        return True, f"用户 [{user_name}] 今日已签到过，无需重复签到"

    soup = BeautifulSoup(res_text, "html.parser")
    formhash_input = soup.find("input", {"name": "formhash"})
    if formhash_input and formhash_input.get("value"):
        formhash = formhash_input["value"]
    else:
        fh_match = re.search(r'name=["\']formhash["\']\s+value=["\']([a-zA-Z0-9]+)["\']', res_text) or re.search(r'formhash=([a-zA-Z0-9]+)', res_text)
        if fh_match:
            formhash = fh_match.group(1)
        else:
            logger.error("[%s] 未能从页面中提取到 formhash", site_label)
            return False, "未能提取到 formhash"

    greetings = [
        "开心每一天，签到打卡！",
        f"论坛越办越好，支持{site_label}！",
        "今天心情非常不错！",
        "每日签到，支持论坛发展！",
        "天天开心，天天向上！"
    ]
    todaysay = random.choice(greetings)

    post_url = f"{domain}/plugin.php?id=dsu_paulsign:sign&operation=qiandao&infloat=1"
    payload = {
        "formhash": formhash,
        "qdxq": "kx",
        "qdmode": "1",
        "todaysay": todaysay,
        "fastreply": "0"
    }

    logger.info("[%s] 提交签到表单 POST: %s (formhash: %s, todaysay: %s)", site_label, post_url, formhash, todaysay)
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
        c_div = sign_soup.find("div", {"class": "c"}) or sign_soup.find("div", {"id": "messagetext"})
        msg = c_div.text.strip() if c_div else sign_text[:120].strip()
        if not msg:
            msg = "签到提交完成"

        if "签到成功" in sign_text or "恭喜你" in sign_text or "获得" in sign_text or "奖励" in sign_text or "今日已签" in sign_text:
            logger.info("[%s] 用户 [%s] 签到成功: %s", site_label, user_name, msg)
            return True, f"用户 [{user_name}] 签到成功: {msg}"
        elif "已经签到过了" in sign_text or "今天已经签到" in sign_text:
            logger.info("[%s] 用户 [%s] 今日已签到过: %s", site_label, user_name, msg)
            return True, f"用户 [{user_name}] 今日已签到过，无需重复签到"
        else:
            logger.warning("[%s] 签到返回提示: %s", site_label, msg)
            return False, f"签到返回提示: {msg}"
    except requests.exceptions.Timeout:
        logger.error("[%s] 提交签到表单超时 (超过 %d 秒)", site_label, REQUEST_TIMEOUT)
        return False, f"签到提交超时 (超过 {REQUEST_TIMEOUT} 秒)"
    except Exception as e:
        logger.error("[%s] 提交签到请求失败: %s", site_label, e)
        return False, f"签到请求提交失败: {e}"

def checkin_cnlang(site_dict: dict) -> Tuple[bool, str]:
    return checkin_discuz_dsu_paulsign(
        site_dict,
        default_domain="https://cnlang.org",
        sign_path="/dsu_paulsign-sign.html?mobile=no",
        site_label="国语视界"
    )

def checkin_ymlyhd(site_dict: dict) -> Tuple[bool, str]:
    return checkin_discuz_dsu_paulsign(
        site_dict,
        default_domain="https://www.ymlyhd.com",
        sign_path="/plugin.php?id=dsu_paulsign:sign",
        site_label="影迷乐园"
    )

def checkin_acgrip(site_dict: dict) -> Tuple[bool, str]:
    return checkin_discuz_dsu_paulsign(
        site_dict,
        default_domain="https://bbs.acgrip.com",
        sign_path="/dsu_paulsign-sign.html",
        site_label="ACG字幕论坛"
    )

def checkin_audiences(site_dict: dict) -> Tuple[bool, str]:
    domain = format_domain(site_dict.get("domain"), default="https://audiences.me")
    cookie_str = site_dict.get("cookies", "")
    cookies = parse_cookies_str(cookie_str)
    
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"{domain}/index.php",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Upgrade-Insecure-Requests": "1"
    }

    session = requests.Session()
    session.headers.update(headers)
    session.cookies.update(cookies)

    attendance_url = f"{domain}/attendance.php"
    site_name = site_dict.get("name", "audiences")

    logger.info("[%s] 发起 GET 访问 NexusPHP 签到页: %s", site_name, attendance_url)
    try:
        res = session.get(attendance_url, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout:
        logger.error("[%s] 访问签到页超时 (超过 %d 秒): %s", site_name, REQUEST_TIMEOUT, attendance_url)
        return False, f"网络请求超时 (超过 {REQUEST_TIMEOUT} 秒)"
    except Exception as e:
        logger.error("[%s] 访问签到页网络失败: %s", site_name, e)
        return False, f"网络请求失败: {e}"

    if is_cloudflare_challenge(res.text, res.status_code):
        logger.warning("[%s] 检测到 Cloudflare 拦截 (HTTP %d)，启动无头浏览器处理...", site_name, res.status_code)
        ok, content, new_cookies = solve_with_invisible_playwright(attendance_url, cookies)
        if not ok:
            logger.error("[%s] 无头浏览器绕过失败: %s", site_name, content)
            return False, f"检测到 Cloudflare 防护，且无头浏览器绕过失败: {content}"
        logger.info("[%s] 无头浏览器成功通过 Cloudflare 质询，已更新会话凭据", site_name)
        session.cookies.update(new_cookies)
        res_text = content
    else:
        res_text = res.text

    # 检查是否登录（NexusPHP 登录失效判定）
    if "login.php" in res_text or "class=\"login\"" in res_text or "userdetails.php" not in res_text:
        logger.warning("[%s] 登录凭据无效或已失效", site_name)
        return False, "Cookies 已失效或未登录（请检查是否包含 c_secure_uid/pass 及 cf_clearance）"

    # 提取用户信息
    user_match = re.search(r'userdetails\.php\?id=\d+[^>]*>.*?<b>(.*?)</b>', res_text)
    user_name = user_match.group(1).strip() if user_match else "NexusPT用户"

    # 提取 audiences 特色的签到卡片或顶部状态
    # 1. 顶部已获得天数/奖励 (如 title="(签到已得22)")
    attended_match = re.search(r'title=[\'"]\(签到已得(\d+)\)[\'"]', res_text)
    attended_days = attended_match.group(1) if attended_match else ""

    # 2. 签到卡片提示 (如 "您今天已经签到过了，请勿重复刷新。")
    card_desc_match = re.search(r'class=[\'"]attendance-card__desc[\'"]>(.*?)</p>', res_text)
    card_desc = card_desc_match.group(1).strip() if card_desc_match else ""

    # 3. 判定签到状态
    if "attendance-card--done" in res_text or "今天已经签到" in res_text or "今日已签到" in res_text or "已经签到过了" in res_text:
        info = f"今日已签到 (连续签到 {attended_days} 天)" if attended_days else (card_desc or "今日已签到")
        logger.info("[%s] 用户 [%s] %s", site_name, user_name, info)
        return True, f"用户 [{user_name}] {info}"

    if "签到成功" in res_text or "获得" in res_text:
        award_match = re.search(r'这是您的第.*?次签到.*?已获得.*?魔力值', res_text) or re.search(r'获得.*?爆米花', res_text)
        award_info = award_match.group(0) if award_match else "签到成功"
        logger.info("[%s] 用户 [%s] 签到成功: %s", site_name, user_name, award_info)
        return True, f"用户 [{user_name}] {award_info}"

    # 未能识别成功特征，避免误报为成功
    soup = BeautifulSoup(res_text, "html.parser")
    title_text = soup.title.string.strip() if soup.title else ""
    logger.warning("[%s] 未能匹配到明确的签到成功特征 (页面标题: %s)", site_name, title_text)
    return False, f"未匹配到明确的签到成功特征 (页面标题: {title_text})"

def execute_checkin_once(site_dict: dict) -> Tuple[bool, str]:
    from pig_checkin.crypto import decrypt_text
    site_dict = dict(site_dict)
    if site_dict.get("cookies"):
        site_dict["cookies"] = decrypt_text(site_dict["cookies"])
    if site_dict.get("username"):
        site_dict["username"] = decrypt_text(site_dict["username"])
    if site_dict.get("password"):
        site_dict["password"] = decrypt_text(site_dict["password"])

    site_type = site_dict.get("site_type", "cnlang")
    if site_type == "cnlang":
        return checkin_cnlang(site_dict)
    elif site_type == "audiences":
        return checkin_audiences(site_dict)
    elif site_type == "ymlyhd":
        return checkin_ymlyhd(site_dict)
    elif site_type == "acgrip":
        return checkin_acgrip(site_dict)
    return False, f"不支持的站点类型: {site_type}"

def execute_checkin(site_dict: dict) -> Tuple[bool, str]:
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
                from pig_checkin.scheduler import compute_next_day_run
                next_run_str = compute_next_day_run(site_dict.get("schedule_type", "random"), site_dict.get("schedule_time", "09:00"))
                with get_db() as conn:
                    conn.execute(
                        "UPDATE sites SET last_status = '签到成功', last_run = ?, next_run = ? WHERE id = ?",
                        (now_str, next_run_str, site_id)
                    )
                    conn.execute(
                        "INSERT INTO logs (site_id, site_name, status, message) VALUES (?, ?, '成功', ?)",
                        (site_id, site_name, full_msg)
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
                    "INSERT INTO logs (site_id, site_name, status, message) VALUES (?, ?, '失败', ?)",
                    (site_id, site_name, log_msg)
                )
            else:
                final_status = "签到失败"
                if site_id:
                    conn.execute("UPDATE sites SET last_status = ?, last_run = ? WHERE id = ?", (final_status, now_str, site_id))
                log_msg = f"尝试 {max_retries + 1} 次均失败，最终错误: {msg}"
                conn.execute(
                    "INSERT INTO logs (site_id, site_name, status, message) VALUES (?, ?, '失败', ?)",
                    (site_id, site_name, log_msg)
                )
            conn.commit()

        if attempt < max_retries:
            logger.info("[%s] 等待 %d 秒后进行第 %d 次尝试...", site_name, retry_interval, attempt + 1)
            time.sleep(retry_interval)

    final_msg = f"尝试 {max_retries + 1} 次均失败，最终错误: {last_msg}"
    logger.error("========== [签到结束-失败] %s: %s ==========", site_name, final_msg)
    return False, final_msg
