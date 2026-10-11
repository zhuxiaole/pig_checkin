SITE_TEMPLATES = {
    "cnlang": {
        "name": "cnlang.org (国语视界)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://cnlang.org",
        "cookie_placeholder": "请粘贴浏览器登录 cnlang.org 后的完整 Cookie 字符串",
    },
    "audiences": {
        "name": "audiences.me (观众 PT)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证 (含 cf_clearance)",
        "default_domain": "https://audiences.me",
        "cookie_placeholder": "请粘贴登录 audiences.me 后的完整 Cookie 字符串 (包含 c_secure_uid、c_secure_pass 及 cf_clearance)",
    }
}

def get_template_auth_type(site_type: str) -> str:
    template = SITE_TEMPLATES.get(site_type, SITE_TEMPLATES["cnlang"])
    return template["auth_type"]
