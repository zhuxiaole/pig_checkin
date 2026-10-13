"""站点模板库。

全部模板来自随程序发布的 site_templates.yaml；代码只保留协议实现
（discuz / nexusphp / yysub），模板描述站点之间的差异：签到路径、请求参数、
以及判定签到状态的关键字与正则。

「启用/停用」只决定后台「添加/编辑网站」下拉框里可选哪些模板，
不影响已存在站点的签到执行。
"""
import copy
import re
import logging
from pathlib import Path

import yaml

logger = logging.getLogger("pig_checkin")

CATALOG_PATH = Path(__file__).resolve().parent / "site_templates.yaml"
SCHEMA_VERSION = 1
MAX_PATTERN_LEN = 500      # 正则长度上限，压缩 ReDoS 空间
MAX_TEXT_LEN = 300
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")

# 元字段：UI 展示与站点保存使用，不参与协议实现
META_FIELDS = ("key", "name", "flow", "auth_type", "auth_label", "default_domain", "cookie_placeholder")

# 以下 *_DEFAULTS 是各协议的默认参数，模板只写需要覆盖的字段。
# 约定：*_patterns 为按顺序尝试的正则数组（取首个命中，无捕获组时取整体匹配）；
#      *_keywords 为子串包含判定；值里的 {site} / {username} / {password} 为运行期占位符。

DISCUZ_DEFAULTS = {
    "sign_path": "/plugin.php?id=dsu_paulsign:sign",
    "submit_path": "/plugin.php?id=dsu_paulsign:sign&operation=qiandao&infloat=1",
    "submit_body": {"qdxq": "kx", "qdmode": "1", "fastreply": "0"},
    "formhash_field": "formhash",
    "greeting_field": "todaysay",
    "greetings": [
        "开心每一天，签到打卡！",
        "论坛越办越好，支持{site}！",
        "今天心情非常不错！",
        "每日签到，支持论坛发展！",
        "天天开心，天天向上！",
    ],
    "sign_form_selectors": [["input", "qdxq"], ["form", "qiandao"]],
    "logged_in_patterns": [r"discuz_uid\s*=\s*['\"]?(\d+)['\"]?"],
    "logged_in_keywords": ["action=logout"],
    "need_login_keywords": ["您需要先登录才能继续本操作", "<title>登录"],
    "user_patterns": [
        r'title="访问我的空间">(.*?)</a>',
        r'class="vwmy"><a [^>]*>(.*?)</a>',
        r'<a [^>]*space-uid-\d+\.html[^>]*title="([^"]+)"',
        r'<span class="user_avt">([^<]+)</span>',
    ],
    "formhash_patterns": [
        r'name=["\']formhash["\']\s+value=["\']([a-zA-Z0-9]+)["\']',
        r"formhash=([a-zA-Z0-9]+)",
    ],
    "already_signed_page_keywords": ["您今天已经签到过了", "已经签到过了", "您今日已经签到", "您累计已签到"],
    "success_keywords": ["签到成功", "恭喜你", "获得", "奖励", "今日已签"],
    "already_signed_keywords": ["已经签到过了", "今天已经签到"],
    "message_selectors": [["div", "c"], ["div", "messagetext"]],
    "encoding": "",
}

NEXUSPHP_DEFAULTS = {
    "sign_path": "/attendance.php",
    "login_fail_keywords": ['login.php', 'class="login"'],
    "login_required_keyword": "userdetails.php",
    "user_patterns": [r"userdetails\.php\?id=\d+[^>]*>.*?<b>(.*?)</b>"],
    "done_keywords": ["attendance-card--done", "今天已经签到", "今日已签到", "已经签到过了"],
    "verify_keywords": ["attendance-card--verify"],
    "success_keywords": ["签到成功"],
    "bonus_patterns": [r'title=[\'"]\(签到已得(\d+)\)[\'"]', r"已签到.*?<span>\+(\d+)</span>"],
    "card_desc_patterns": [r'class=[\'"]attendance-card__desc[\'"]>(.*?)</p>'],
    "award_patterns": [r"这是您的第.*?次签到.*?已获得.*?魔力值"],
    "encoding": "",
}

YYSUB_DEFAULTS = {
    "login_path": "/User/Login/ajaxLogin",
    "info_path": "/user/login/getCurUserTopInfo",
    "login_body": {
        "account": "{username}",
        "email": "{username}",
        "password": "{password}",
        "remember": "1",
    },
    "login_fail_status": [0, "0", 4001, "4001"],
    "unauth_status": [4001, "4001"],
    "success_fields": ["data.usercount.cont_login", "data.userinfo.point"],
    "ok_status": [1, 200, "1", "200"],
}

FLOW_DEFAULTS: dict[str, dict] = {
    "discuz": DISCUZ_DEFAULTS,
    "nexusphp": NEXUSPHP_DEFAULTS,
    "yysub": YYSUB_DEFAULTS,
}


# ---------------------------------------------------------------- 校验

def _scalar_ok(value) -> bool:
    return isinstance(value, (str, int, float, bool))


def _pattern_list(key: str, value) -> list[str]:
    if key.endswith("_patterns") and isinstance(value, list):
        return [p for p in value if isinstance(p, str)]
    if key.endswith("_pattern") and isinstance(value, str):
        return [value]
    return []


def _check_flow_fields(flow: str, raw: dict) -> list[str]:
    errors = []
    for key, default in FLOW_DEFAULTS[flow].items():
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(default, dict):
            if not isinstance(value, dict) or not all(isinstance(k, str) and _scalar_ok(v) for k, v in value.items()):
                errors.append(f"{key} 需为「字符串 → 标量」的对象")
                continue
        elif isinstance(default, list):
            if not isinstance(value, list) or not all(_scalar_ok(v) for v in value):
                errors.append(f"{key} 需为数组")
                continue
        elif not isinstance(value, str):
            errors.append(f"{key} 需为字符串")
            continue

        for pattern in _pattern_list(key, value):
            if len(pattern) > MAX_PATTERN_LEN:
                errors.append(f"{key} 正则过长（上限 {MAX_PATTERN_LEN} 字符）")
                continue
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(f"{key} 正则非法：{e}")

    for key in ("sign_form_selectors", "message_selectors"):
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, list) or not all(
            isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item) for item in value
        ):
            errors.append(f"{key} 需为 [[\"标签\", \"name\"], ...] 形式")
    return errors


def validate_template(raw) -> list[str]:
    """返回错误列表，空列表表示模板合法。"""
    if not isinstance(raw, dict):
        return ["模板需为键值对象"]

    errors: list[str] = []
    key = raw.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        errors.append("key 需为 1-32 位小写字母/数字/下划线")

    flow = raw.get("flow")
    if flow not in FLOW_DEFAULTS:
        return errors + [f"flow 需为 {'/'.join(FLOW_DEFAULTS)} 之一"]

    if raw.get("auth_type") not in ("cookie", "password"):
        errors.append("auth_type 需为 cookie 或 password")

    for field in ("name", "auth_label", "cookie_placeholder"):
        value = raw.get(field, "")
        if not isinstance(value, str) or (field != "cookie_placeholder" and not value.strip()):
            errors.append(f"{field} 不能为空")
        elif len(value) > MAX_TEXT_LEN:
            errors.append(f"{field} 超过 {MAX_TEXT_LEN} 字符")

    domain = raw.get("default_domain", "")
    if not isinstance(domain, str) or not domain.startswith(("http://", "https://")) or not domain.split("//", 1)[1]:
        errors.append("default_domain 需为含协议的完整域名，如 https://example.com")

    if flow != "yysub":
        sign_path = raw.get("sign_path", "")
        if not isinstance(sign_path, str) or not sign_path.startswith("/"):
            errors.append("sign_path 需以 / 开头的站内路径")

    unknown = sorted(set(raw) - set(META_FIELDS) - set(FLOW_DEFAULTS[flow]))
    if unknown:
        errors.append(f"存在未知字段：{', '.join(unknown)}")

    errors.extend(_check_flow_fields(flow, raw))
    return errors


def resolve_template(raw: dict) -> dict:
    """合并协议默认值与模板覆盖项，得到完整可用的模板。"""
    out = copy.deepcopy(FLOW_DEFAULTS[raw["flow"]])
    out.update(raw)
    return out


# ---------------------------------------------------------------- 模板库

def parse_catalog(text: str) -> tuple[list[dict], list[str]]:
    """解析模板库文档，返回 (合法模板列表, 错误说明列表)。"""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("模板库根节点需为键值对象")
    if doc.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"模板库 schema 需为 {SCHEMA_VERSION}，实际为 {doc.get('schema')!r}")
    items = doc.get("templates")
    if not isinstance(items, list):
        raise ValueError("模板库缺少 templates 列表")

    valid, errors = [], []
    for raw in items:
        errs = validate_template(raw)
        if errs:
            errors.append(f"{raw.get('key', '?') if isinstance(raw, dict) else '?'}: {'; '.join(errs)}")
        else:
            valid.append(raw)
    return valid, errors


_catalog_cache: list[dict] | None = None


def builtin_catalog() -> list[dict]:
    global _catalog_cache
    if _catalog_cache is None:
        try:
            templates, errors = parse_catalog(CATALOG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("[模板] 模板库解析失败: %s", e)
            templates, errors = [], []
        for err in errors:
            logger.warning("[模板] 已跳过不合法模板 %s", err)
        _catalog_cache = templates
    return _catalog_cache


def all_templates() -> dict[str, dict]:
    """全部模板（已合并协议默认值）。"""
    return {raw["key"]: resolve_template(raw) for raw in builtin_catalog()}


def enabled_keys() -> set[str]:
    from pig_checkin.db import get_db
    with get_db() as conn:
        return {row[0] for row in conn.execute("SELECT key FROM enabled_templates").fetchall()}


def usage_counts() -> dict[str, int]:
    """各模板当前被多少个站点引用。"""
    from pig_checkin.db import get_db
    with get_db() as conn:
        return {row[0]: row[1] for row in conn.execute("SELECT site_type, COUNT(*) FROM sites GROUP BY site_type")}


def enabled_templates() -> dict[str, dict]:
    """后台「添加/编辑网站」可选用的模板。默认一个都不启用，需在模板库中手动启用。"""
    enabled = enabled_keys()
    return {key: tpl for key, tpl in all_templates().items() if key in enabled}


def set_template_enabled(key: str, enabled: bool) -> str | None:
    """启用/停用模板。返回 None 表示成功，否则返回失败原因。

    正被站点使用的模板不允许停用：停用后它仍会出现在编辑下拉框里（否则旧站点无法保存），
    但新建列表却看不到，容易让人误以为停用没生效。
    """
    tpl = get_template(key)
    if not tpl:
        return f"模板库中不存在 [{key}]"
    if not enabled:
        used = usage_counts().get(key, 0)
        if used:
            return f"模板 [{tpl['name']}] 正被 {used} 个站点使用，无法停用；请先删除这些站点或改用其它模板"

    from pig_checkin.db import get_db
    with get_db() as conn:
        if enabled:
            conn.execute("INSERT OR IGNORE INTO enabled_templates (key) VALUES (?)", (key,))
        else:
            conn.execute("DELETE FROM enabled_templates WHERE key = ?", (key,))
        conn.commit()
    return None


def get_template(key: str) -> dict | None:
    """按 key 取模板（与启用状态无关，保证已存在站点照常签到）。"""
    if not key:
        return None
    return all_templates().get(key)
