import os
import re
import time
import random
import requests
from bs4 import BeautifulSoup
from typing import Tuple
from urllib.parse import urlparse

os.environ.setdefault("INVISIBLE_PLAYWRIGHT_CACHE_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".cache", "inv_pw")))

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

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
        with InvisiblePlaywright(headless=True) as pw:
            page = pw.new_page()
            if cookies:
                pw_cookies = [{"name": k, "value": v, "url": url} for k, v in cookies.items()]
                try:
                    page.context.add_cookies(pw_cookies)
                except Exception:
                    pass
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)
            content = page.content()
            new_cookies = {c["name"]: c["value"] for c in page.context.cookies()}
            return True, content, new_cookies
    except Exception as e:
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

    try:
        res = session.get(sign_page_url, timeout=15)
    except Exception as e:
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
        ok, content, new_cookies = solve_with_invisible_playwright(sign_page_url, cookies)
        if not ok:
            return False, f"检测到 Cloudflare 防护，且无头浏览器绕过失败: {content}"
        session.cookies.update(new_cookies)
        res_text = content

    if "您需要先登录才能继续本操作" in res_text or "<title>登录" in res_text or ("title=\"访问我的空间\"" not in res_text and "member.php?mod=logging" in res_text):
        return False, "Cookies 已失效或未登录，请更新 Cookies"

    user_match = re.search(r'title="访问我的空间">(.*?)</a>', res_text) or re.search(r'class="vwmy"><a [^>]*>(.*?)</a>', res_text)
    user_name = user_match.group(1).strip() if user_match else site_dict.get("name", "用户")

    if "今日已签" in res_text or "您今天已经签到过了" in res_text or "您今日已经签到" in res_text:
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

    try:
        if encoding in ("gbk", "gb2312"):
            import urllib.parse
            data_encoded = urllib.parse.urlencode(payload, encoding=encoding).encode("ascii")
            sign_res = session.post(
                post_url,
                data=data_encoded,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15
            )
        else:
            sign_res = session.post(post_url, data=payload, timeout=15)

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
            return True, f"用户 [{user_name}] 签到成功: {msg}"
        elif "已经签到过了" in sign_text or "今天已经签到" in sign_text:
            return True, f"用户 [{user_name}] 今日已签到过，无需重复签到"
        else:
            return False, f"签到返回提示: {msg}"
    except Exception as e:
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

    try:
        res = session.get(attendance_url, timeout=15)
    except Exception as e:
        return False, f"网络请求失败: {e}"

    if is_cloudflare_challenge(res.text, res.status_code):
        ok, content, new_cookies = solve_with_invisible_playwright(attendance_url, cookies)
        if not ok:
            return False, f"检测到 Cloudflare 防护，且无头浏览器绕过失败: {content}"
        session.cookies.update(new_cookies)
        res_text = content
    else:
        res_text = res.text

    # 检查是否登录（NexusPHP 登录失效判定）
    if "login.php" in res_text or "class=\"login\"" in res_text or "userdetails.php" not in res_text:
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
        return True, f"用户 [{user_name}] {info}"

    if "签到成功" in res_text or "获得" in res_text:
        award_match = re.search(r'这是您的第.*?次签到.*?已获得.*?魔力值', res_text) or re.search(r'获得.*?爆米花', res_text)
        award_info = award_match.group(0) if award_match else "签到成功"
        return True, f"用户 [{user_name}] {award_info}"

    # 未能识别成功特征，避免误报为成功
    soup = BeautifulSoup(res_text, "html.parser")
    title_text = soup.title.string.strip() if soup.title else ""
    return False, f"未匹配到明确的签到成功特征 (页面标题: {title_text})"

def execute_checkin_once(site_dict: dict) -> Tuple[bool, str]:
    site_type = site_dict.get("site_type", "cnlang")
    if site_type == "cnlang":
        return checkin_cnlang(site_dict)
    elif site_type == "audiences":
        return checkin_audiences(site_dict)
    elif site_type == "ymlyhd":
        return checkin_ymlyhd(site_dict)
    return False, f"不支持的站点类型: {site_type}"

def execute_checkin(site_dict: dict) -> Tuple[bool, str]:
    max_retries = max(0, int(site_dict.get("max_retries", 3) or 0))
    retry_interval = max(1, int(site_dict.get("retry_interval", 60) or 1))

    last_msg = ""
    for attempt in range(max_retries + 1):
        ok, msg = execute_checkin_once(site_dict)
        if ok:
            if attempt > 0:
                return True, f"{msg} (第 {attempt} 次重试成功)"
            return True, msg
        last_msg = msg
        if attempt < max_retries:
            time.sleep(retry_interval)
    return False, f"尝试 {max_retries + 1} 次均失败，最终错误: {last_msg}"
