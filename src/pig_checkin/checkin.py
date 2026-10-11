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

def format_domain(domain: str) -> str:
    domain = (domain or "").strip()
    if not domain:
        return "https://cnlang.org"
    if not domain.startswith("http://") and not domain.startswith("https://"):
        domain = "https://" + domain
    return domain.rstrip("/")

def checkin_cnlang(site_dict: dict) -> Tuple[bool, str]:
    domain = format_domain(site_dict.get("domain", "https://cnlang.org"))
    cookie_str = site_dict.get("cookies", "")
    cookies = parse_cookies_str(cookie_str)
    
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"{domain}/dsu_paulsign-sign.html?mobile=no",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Upgrade-Insecure-Requests": "1"
    }

    session = requests.Session()
    session.headers.update(headers)
    session.cookies.update(cookies)

    sign_page_url = f"{domain}/dsu_paulsign-sign.html?mobile=no"

    try:
        res = session.get(sign_page_url, timeout=15)
    except Exception as e:
        return False, f"网络请求失败: {e}"

    if is_cloudflare_challenge(res.text, res.status_code):
        ok, content, new_cookies = solve_with_invisible_playwright(sign_page_url, cookies)
        if not ok:
            return False, f"检测到反爬/CF，且无头浏览器绕过失败: {content}"
        session.cookies.update(new_cookies)
        res_text = content
    else:
        res_text = res.text

    if "您需要先登录才能继续本操作" in res_text or "title=\"访问我的空间\"" not in res_text:
        return False, "Cookies 已失效或未登录，请重新配置 Cookies 或账号密码"

    user_match = re.search(r'title="访问我的空间">(.*?)</a>', res_text)
    user_name = user_match.group(1) if user_match else "未知用户"

    if "今日已签" in res_text or "您今天已经签到过了" in res_text:
        return True, f"用户 [{user_name}] 今日已签到过，无需重复签到"

    soup = BeautifulSoup(res_text, "html.parser")
    formhash_input = soup.find("input", {"name": "formhash"})
    if not formhash_input or not formhash_input.get("value"):
        return False, "未能提取到 formhash"
    formhash = formhash_input["value"]

    greetings = ["开心每一天，签到打卡！", "论坛越办越好，支持国语视界！", "今天心情非常不错！", "每日签到，支持论坛发展！", "天天开心，天天向上！"]
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
        sign_res = session.post(post_url, data=payload, timeout=15)
        sign_soup = BeautifulSoup(sign_res.text, "html.parser")
        c_div = sign_soup.find("div", {"class": "c"})
        msg = c_div.text.strip() if c_div else sign_res.text[:100].strip()
        if not msg:
            msg = "签到提交完成"
        return True, f"用户 [{user_name}] 签到成功: {msg}"
    except Exception as e:
        return False, f"签到请求提交失败: {e}"

def execute_checkin_once(site_dict: dict) -> Tuple[bool, str]:
    site_type = site_dict.get("site_type", "cnlang")
    if site_type == "cnlang":
        return checkin_cnlang(site_dict)
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
