SITE_TEMPLATES = {
    "cnlang": {
        "name": "cnlang.org (国语视界)",
        "auth_type": "cookie",
        "auth_label": "Cookies 登录",
        "default_domain": "https://cnlang.org",
        "cookie_placeholder": "请粘贴浏览器登录 cnlang.org 后的完整 Cookie 字符串",
    }
}

def get_template_auth_type(site_type: str) -> str:
    template = SITE_TEMPLATES.get(site_type, SITE_TEMPLATES["cnlang"])
    return template["auth_type"]
