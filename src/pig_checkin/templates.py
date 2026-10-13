"""站点模板：内置目录 + 远程模板库 + 本地导入库。

一个模板就是一份 JSON，声明某站点在所属协议（flow）下的**差异点**：
签到路径、请求参数、以及判定签到状态的关键字与正则。
协议本身（步骤顺序、请求方法）由 checkin.py 的三种实现固定，模板只覆盖可变量。

模板来源优先级：本地导入库 > 内置目录（同 key 时导入的覆盖内置）。
"""
import copy
import json
import os
import re
import logging
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, List, Tuple

import requests

logger = logging.getLogger("pig_checkin")

BUILTIN_PATH = Path(__file__).resolve().parent / "site_templates.json"
SCHEMA_VERSION = 1
MAX_DOC_BYTES = 1024 * 1024      # 远程模板库体积上限，防超大响应
MAX_PATTERN_LEN = 500            # 正则长度上限，压缩 ReDoS 空间
MAX_TEXT_LEN = 300
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")
REGISTRY_TIMEOUT = int(os.environ.get("PIG_REQUEST_TIMEOUT", 30))
DEFAULT_REGISTRY_URL = "https://raw.githubusercontent.com/zhuxiaole/pig_checkin/main/src/pig_checkin/site_templates.json"

# 元字段：UI 展示与站点保存使用，不参与协议实现
META_FIELDS = ("key", "name", "flow", "auth_type", "auth_label", "default_domain", "cookie_placeholder", "label")

# 以下 *_DEFAULTS 是各协议的默认参数，模板只写需要覆盖的字段。
# 约定：*_patterns 为按顺序尝试的正则数组，取首个命中；*_keywords 为子串包含判定。
# 值里的 {site} / {username} / {password} 为运行期占位符。

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

FLOW_DEFAULTS: Dict[str, dict] = {
    "discuz": DISCUZ_DEFAULTS,
    "nexusphp": NEXUSPHP_DEFAULTS,
    "yysub": YYSUB_DEFAULTS,
}


# ---------------------------------------------------------------- 校验

def _scalar_ok(value) -> bool:
    return isinstance(value, (str, int, float, bool))


def _pattern_list(key: str, value) -> List[str]:
    if key.endswith("_patterns") and isinstance(value, list):
        return [p for p in value if isinstance(p, str)]
    if key.endswith("_pattern") and isinstance(value, str):
        return [value]
    return []


def _check_flow_fields(flow: str, raw: dict) -> List[str]:
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


def validate_template(raw) -> List[str]:
    """返回错误列表，空列表表示模板合法。远程模板库内容不可信，必须校验。"""
    if not isinstance(raw, dict):
        return ["模板需为 JSON 对象"]

    errors: List[str] = []
    key = raw.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        errors.append("key 需为 1-32 位小写字母/数字/下划线")

    flow = raw.get("flow")
    if flow not in FLOW_DEFAULTS:
        return errors + [f"flow 需为 {'/'.join(FLOW_DEFAULTS)} 之一"]

    if raw.get("auth_type") not in ("cookie", "password"):
        errors.append("auth_type 需为 cookie 或 password")

    for field in ("name", "label", "auth_label", "cookie_placeholder"):
        value = raw.get(field, "")
        if not isinstance(value, str) or (field != "cookie_placeholder" and not value.strip()):
            errors.append(f"{field} 不能为空")
        elif len(value) > MAX_TEXT_LEN:
            errors.append(f"{field} 超过 {MAX_TEXT_LEN} 字符")

    domain = raw.get("default_domain", "")
    if not isinstance(domain, str) or not domain.startswith(("http://", "https://")) or not urlparse(domain).netloc:
        errors.append("default_domain 需为含协议的完整域名，如 https://example.com")

    if flow != "yysub":
        sign_path = raw.get("sign_path", "")
        if not isinstance(sign_path, str) or not sign_path.startswith("/"):
            errors.append("sign_path 需以 / 开头的站内路径")

    allowed = set(META_FIELDS) | set(FLOW_DEFAULTS[flow])
    unknown = sorted(set(raw) - allowed)
    if unknown:
        errors.append(f"存在未知字段：{', '.join(unknown)}")

    errors.extend(_check_flow_fields(flow, raw))
    return errors


def resolve_template(raw: dict) -> dict:
    """合并协议默认值与模板覆盖项，得到完整可用的模板。"""
    out = copy.deepcopy(FLOW_DEFAULTS[raw["flow"]])
    out.update(raw)
    return out


# ---------------------------------------------------------------- 目录解析

def parse_catalog(text: str) -> Tuple[List[dict], List[str]]:
    """解析模板库文档，返回 (合法模板列表, 错误说明列表)。"""
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ValueError("模板库根节点需为 JSON 对象")
    if doc.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"模板库 schema 需为 {SCHEMA_VERSION}，实际为 {doc.get('schema')!r}")
    items = doc.get("templates")
    if not isinstance(items, list):
        raise ValueError("模板库缺少 templates 数组")

    valid, errors = [], []
    for raw in items:
        errs = validate_template(raw)
        if errs:
            errors.append(f"{raw.get('key', '?') if isinstance(raw, dict) else '?'}: {'; '.join(errs)}")
        else:
            valid.append(raw)
    return valid, errors


_builtin_cache: List[dict] | None = None


def builtin_catalog() -> List[dict]:
    global _builtin_cache
    if _builtin_cache is None:
        try:
            templates, errors = parse_catalog(BUILTIN_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("[模板] 内置模板库解析失败: %s", e)
            templates, errors = [], []
        for err in errors:
            logger.warning("[模板] 内置模板已跳过 %s", err)
        _builtin_cache = templates
    return _builtin_cache


# ---------------------------------------------------------------- 本地导入库

def local_raw_templates() -> Dict[str, dict]:
    from pig_checkin.db import get_db
    with get_db() as conn:
        rows = conn.execute("SELECT key, json FROM template_library").fetchall()
    out = {}
    for row in rows:
        try:
            raw = json.loads(row["json"])
        except Exception as e:
            logger.warning("[模板] 本地模板 %s 解析失败已忽略: %s", row["key"], e)
            continue
        errs = validate_template(raw)
        if errs:
            logger.warning("[模板] 本地模板 %s 校验失败已忽略: %s", row["key"], "; ".join(errs))
            continue
        out[row["key"]] = raw
    return out


def save_template(raw: dict, source: str = "imported") -> None:
    from pig_checkin.db import get_db
    with get_db() as conn:
        conn.execute(
            "INSERT INTO template_library (key, json, source) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET json = excluded.json, source = excluded.source",
            (raw["key"], json.dumps(raw, ensure_ascii=False), source),
        )
        conn.commit()


def delete_template(key: str) -> bool:
    from pig_checkin.db import get_db
    with get_db() as conn:
        cur = conn.execute("DELETE FROM template_library WHERE key = ?", (key,))
        conn.commit()
        return cur.rowcount > 0


def all_templates() -> Dict[str, dict]:
    """内置目录 + 本地导入库合并后的全部可用模板（已解析默认值）。"""
    out = {t["key"]: resolve_template(t) for t in builtin_catalog()}
    for key, raw in local_raw_templates().items():
        out[key] = resolve_template(raw)
    return out


def get_template(key: str) -> dict | None:
    if not key:
        return None
    return all_templates().get(key)


def get_template_auth_type(site_type: str) -> str:
    tpl = get_template(site_type)
    return tpl["auth_type"] if tpl else "cookie"


# ---------------------------------------------------------------- 远程模板库

def registry_url() -> str:
    return (os.environ.get("PIG_TEMPLATE_REGISTRY_URL") or "").strip() or DEFAULT_REGISTRY_URL


def fetch_registry(url: str | None = None) -> Tuple[bool, dict]:
    """拉取远程模板库文档。返回 (是否成功, 结果)。不抛异常，失败信息放 error 字段。"""
    target = (url or registry_url()).strip()
    result = {"url": target, "templates": [], "errors": [], "error": ""}
    if not target.startswith(("http://", "https://")):
        result["error"] = "模板库地址需以 http:// 或 https:// 开头"
        return False, result
    try:
        res = requests.get(target, timeout=REGISTRY_TIMEOUT)
        res.raise_for_status()
        if len(res.content) > MAX_DOC_BYTES:
            raise ValueError(f"模板库文件超过 {MAX_DOC_BYTES} 字节上限")
        templates, errors = parse_catalog(res.content.decode("utf-8"))
        result["templates"] = templates
        result["errors"] = errors
        return True, result
    except Exception as e:
        result["error"] = str(e)
        return False, result
