# 站点模板配置：每个站点在此声明一次，UI 展示与签到流程均从此处读取。
#
# flow 可选值（对应 checkin.FLOWS 中的协议实现）：
#   discuz   - Discuz! 论坛 dsu_paulsign 插件签到（需 sign_path）
#   nexusphp - NexusPHP PT 站 attendance.php 签到（需 sign_path）
#   yysub    - yysub.cc 原生 JSON API 登录签到
#
# sign_path 为签到页相对路径；label 为日志中显示的站点简称。
# 各协议的具体步骤、请求参数位置与成功判定见 SUPPORTED_SITES.md。
SITE_TEMPLATES = {
    "cnlang": {
        "name": "cnlang.org (国语视界)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://cnlang.org",
        "cookie_placeholder": "请粘贴浏览器登录 cnlang.org 后的完整 Cookie 字符串",
        "flow": "discuz",
        "sign_path": "/dsu_paulsign-sign.html?mobile=no",
        "label": "国语视界",
    },
    "audiences": {
        "name": "audiences.me (观众 PT)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://audiences.me",
        "cookie_placeholder": "请粘贴登录 audiences.me 后的完整 Cookie 字符串 (包含 c_secure_uid、c_secure_pass 及 cf_clearance)",
        "flow": "nexusphp",
        "sign_path": "/attendance.php",
        "label": "audiences",
    },
    "ymlyhd": {
        "name": "ymlyhd.com (影迷乐园)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://www.ymlyhd.com",
        "cookie_placeholder": "请粘贴登录 ymlyhd.com 后的完整 Cookie 字符串",
        "flow": "discuz",
        "sign_path": "/plugin.php?id=dsu_paulsign:sign",
        "label": "影迷乐园",
    },
    "acgrip": {
        "name": "bbs.acgrip.com (ACG字幕论坛)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://bbs.acgrip.com",
        "cookie_placeholder": "请粘贴登录 bbs.acgrip.com 后的完整 Cookie 字符串",
        "flow": "discuz",
        "sign_path": "/dsu_paulsign-sign.html",
        "label": "ACG字幕论坛",
    },
    "xingyunge": {
        "name": "pt.xingyungept.org (星运阁 PT)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://pt.xingyungept.org",
        "cookie_placeholder": "请粘贴登录 pt.xingyungept.org 后的完整 Cookie 字符串 (包含 c_secure_uid、c_secure_pass 及 cf_clearance)",
        "flow": "nexusphp",
        "sign_path": "/attendance.php",
        "label": "星运阁",
    },
    "novahd": {
        "name": "pt.novahd.top (NovaHD PT)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://pt.novahd.top",
        "cookie_placeholder": "请粘贴登录 pt.novahd.top 后的完整 Cookie 字符串 (包含 c_secure_uid、c_secure_pass 及 cf_clearance)",
        "flow": "nexusphp",
        "sign_path": "/attendance.php",
        "label": "NovaHD",
    },
    "ssdforum": {
        "name": "ssdforum.org (SSD论坛)",
        "auth_type": "cookie",
        "auth_label": "Cookies 认证",
        "default_domain": "https://ssdforum.org",
        "cookie_placeholder": "请粘贴登录 ssdforum.org 后的完整 Cookie 字符串",
        "flow": "discuz",
        "sign_path": "/plugin.php?id=dsu_paulsign:sign",
        "label": "SSD论坛",
    },
    "yysub": {
        "name": "yysub.cc (YYeTs字幕组)",
        "auth_type": "password",
        "auth_label": "账号 & 密码认证",
        "default_domain": "https://yysub.cc",
        "cookie_placeholder": "（可选）若有需要可填写 Cookie",
        "flow": "yysub",
        "label": "YYeTs字幕组",
    },
}

def get_template_auth_type(site_type: str) -> str:
    return SITE_TEMPLATES.get(site_type, {}).get("auth_type", "cookie")
