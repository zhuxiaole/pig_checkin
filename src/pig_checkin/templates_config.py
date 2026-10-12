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
        "auth_label": "Cookies 认证",
        "default_domain": "https://audiences.me",
        "cookie_placeholder": "请粘贴登录 audiences.me 后的完整 Cookie 字符串 (包含 c_secure_uid、c_secure_pass 及 cf_clearance)",
    },
    "ymlyhd": {
        "name": "ymlyhd.com (影迷乐园)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://www.ymlyhd.com",
        "cookie_placeholder": "请粘贴登录 ymlyhd.com 后的完整 Cookie 字符串",
    },
    "acgrip": {
        "name": "bbs.acgrip.com (ACG字幕论坛)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://bbs.acgrip.com",
        "cookie_placeholder": "请粘贴登录 bbs.acgrip.com 后的完整 Cookie 字符串",
    },
    "xingyunge": {
        "name": "pt.xingyungept.org (星运阁 PT)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://pt.xingyungept.org",
        "cookie_placeholder": "请粘贴登录 pt.xingyungept.org 后的完整 Cookie 字符串 (包含 c_secure_uid、c_secure_pass 及 cf_clearance)",
    },
    "novahd": {
        "name": "pt.novahd.top (NovaHD PT)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://pt.novahd.top",
        "cookie_placeholder": "请粘贴登录 pt.novahd.top 后的完整 Cookie 字符串 (包含 c_secure_uid、c_secure_pass 及 cf_clearance)",
    },
    "ssdforum": {
        "name": "ssdforum.org (SSD论坛)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://ssdforum.org",
        "cookie_placeholder": "请粘贴登录 ssdforum.org 后的完整 Cookie 字符串",
    },
    "yysub": {
        "name": "yysub.cc (YYeTs字幕组)",
        "auth_type": "password",
        "auth_label": "账号 & 密码认证",
        "default_domain": "https://yysub.cc",
        "cookie_placeholder": "（可选）若有需要可填写 Cookie",
    }
}

def get_template_auth_type(site_type: str) -> str:
    template = SITE_TEMPLATES.get(site_type, SITE_TEMPLATES["cnlang"])
    return template["auth_type"]
