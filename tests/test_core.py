import yaml

import pig_checkin.checkin as ck
from pig_checkin.checkin import parse_cookies_str, FLOWS
from pig_checkin import templates as tl
from pig_checkin.scheduler import compute_initial_next_run, compute_next_day_run
from pig_checkin.db import init_db, get_db
from pig_checkin.app import APP_VERSION

BASE = {
    "key": "demo",
    "name": "演示站",
    "flow": "discuz",
    "auth_type": "cookie",
    "auth_label": "Cookies 认证",
    "default_domain": "https://demo.example",
    "cookie_placeholder": "",
    "sign_path": "/sign",
}


def test_00_initialize_db():
    init_db()


def test_cookie_parsing():
    assert parse_cookies_str("a=1; b=2; c=val=ue") == {"a": "1", "b": "2", "c": "val=ue"}
    assert parse_cookies_str("") == {}


def test_catalog_valid_and_named_without_url():
    catalog = tl.builtin_catalog()
    assert catalog, "模板库为空"
    for raw in catalog:
        assert tl.validate_template(raw) == [], f"{raw.get('key')} 不合法"
        assert raw["name"].startswith("http") is False, f"{raw['key']} 名称不应包含网址"
        assert "." not in raw["name"] or "PT" in raw["name"], f"{raw['key']} 名称疑似残留域名"


def test_validation_rejects_bad_templates():
    assert tl.validate_template(BASE) == []
    assert tl.validate_template({**BASE, "key": "Bad Key"}), "key 含非法字符应被拒绝"
    assert tl.validate_template({**BASE, "flow": "unknown"}), "未知 flow 应被拒绝"
    assert tl.validate_template({**BASE, "auth_type": "sms"}), "未知 auth_type 应被拒绝"
    assert tl.validate_template({**BASE, "default_domain": "demo.example"}), "缺协议域名应被拒绝"
    assert tl.validate_template({**BASE, "sign_path": "sign"}), "sign_path 缺前导 / 应被拒绝"
    assert tl.validate_template({**BASE, "label": "旧字段"}), "未知字段应被拒绝"
    assert tl.validate_template({**BASE, "formhash_patterns": ["("]}), "非法正则应被拒绝"
    assert tl.validate_template({**BASE, "formhash_patterns": ["x" * 600]}), "超长正则应被拒绝"
    assert tl.validate_template({**BASE, "success_keywords": "签到成功"}), "非数组应被拒绝"
    assert tl.validate_template({**BASE, "submit_body": {"a": ["b"]}}), "非标量对象值应被拒绝"
    # yysub 不需要 sign_path
    assert tl.validate_template({"key": "demo2", "name": "演示", "flow": "yysub", "auth_type": "password",
                                 "auth_label": "账号", "default_domain": "https://demo.example",
                                 "cookie_placeholder": ""}) == []


def test_resolve_fills_protocol_defaults():
    tpl = tl.resolve_template(BASE)
    assert tpl["sign_path"] == "/sign", "站点配置应覆盖协议默认值"
    assert tpl["submit_path"] == tl.DISCUZ_DEFAULTS["submit_path"], "未覆盖字段应取协议默认值"
    assert tpl["formhash_field"] == "formhash"
    # 默认值必须是深拷贝，调用方不得污染协议默认配置
    tpl["submit_body"]["qdxq"] = "mutated"
    assert tl.DISCUZ_DEFAULTS["submit_body"]["qdxq"] == "kx", "协议默认配置被污染"


def test_parse_catalog():
    valid, errors = tl.parse_catalog(yaml.safe_dump({"schema": 1, "templates": [
        {**BASE, "key": "ok"},
    ]}, allow_unicode=True))
    assert len(valid) == 1 and errors == []

    valid, errors = tl.parse_catalog("schema: 1\ntemplates:\n  - key: bad\n")
    assert valid == [] and errors, "非法模板应进 errors 而非 valid"

    for bad_doc in ("[]", "schema: 2\ntemplates: []\n", "schema: 1\n"):
        try:
            tl.parse_catalog(bad_doc)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad_doc!r} 应抛出 ValueError")


def test_enable_disable():
    key = "cnlang"
    assert key in tl.enabled_templates(), "默认应已启用"
    try:
        assert tl.set_template_enabled(key, False) is True
        assert key not in tl.enabled_templates(), "停用后不应出现在可选模板中"
        assert key in tl.all_templates(), "停用不应从模板库移除"
        assert tl.get_template(key) is not None, "停用后已存在站点仍须能签到"
    finally:
        tl.set_template_enabled(key, True)
    assert key in tl.enabled_templates()
    assert tl.set_template_enabled("no-such-key", True) is False


def test_all_templates_and_dispatch():
    merged = tl.all_templates()
    for key in ("cnlang", "audiences", "acgrip", "yysub"):
        assert key in merged, f"缺少模板 {key}"
    assert tl.get_template("no-such-key") is None

    seen = {}
    orig = ck.FLOWS["nexusphp"]
    ck.FLOWS["nexusphp"] = lambda sd, tpl: (seen.update(tpl=tpl) or (True, "ok"))
    try:
        assert ck.execute_checkin_once({"site_type": "audiences"}) == (True, "ok")
        assert seen["tpl"]["flow"] == "nexusphp"
        assert seen["tpl"]["sign_path"] == "/attendance.php"
    finally:
        ck.FLOWS["nexusphp"] = orig
    assert ck.execute_checkin_once({"site_type": "no-such-site"}) == (False, "不支持的站点类型: no-such-site")


def test_scheduler_and_db():
    assert len(compute_initial_next_run("fixed", "12:00")) == 19
    assert len(compute_next_day_run("random", "09:00")) == 19
    with get_db() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert bool(APP_VERSION)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("OK")
