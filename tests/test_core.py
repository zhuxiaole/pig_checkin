import json

import pig_checkin.checkin as ck
from pig_checkin.checkin import parse_cookies_str, FLOWS
from pig_checkin import templates as tl
from pig_checkin.scheduler import compute_initial_next_run, compute_next_day_run
from pig_checkin.db import init_db, get_db
from pig_checkin.app import APP_VERSION


def test_00_initialize_db():
    init_db()


def test_cookie_parsing():
    assert parse_cookies_str("a=1; b=2; c=val=ue") == {"a": "1", "b": "2", "c": "val=ue"}
    assert parse_cookies_str("") == {}


def test_builtin_catalog_valid():
    catalog = tl.builtin_catalog()
    assert catalog, "内置模板库为空"
    for raw in catalog:
        assert tl.validate_template(raw) == [], f"{raw.get('key')} 内置模板不合法"


def test_validation_rejects_bad_templates():
    base = {
        "key": "demo", "name": "Demo", "label": "演示", "flow": "discuz",
        "auth_type": "cookie", "auth_label": "Cookies 认证",
        "default_domain": "https://demo.example", "cookie_placeholder": "",
        "sign_path": "/sign",
    }
    assert tl.validate_template(base) == []

    assert tl.validate_template({**base, "key": "Bad Key"}), "key 含非法字符应被拒绝"
    assert tl.validate_template({**base, "flow": "unknown"}), "未知 flow 应被拒绝"
    assert tl.validate_template({**base, "auth_type": "sms"}), "未知 auth_type 应被拒绝"
    assert tl.validate_template({**base, "default_domain": "demo.example"}), "缺协议域名应被拒绝"
    assert tl.validate_template({**base, "sign_path": "sign"}), "sign_path 缺前导 / 应被拒绝"
    assert tl.validate_template({**base, "nonexistent_field": 1}), "未知字段应被拒绝"
    assert tl.validate_template({**base, "formhash_patterns": ["("]}), "非法正则应被拒绝"
    assert tl.validate_template({**base, "formhash_patterns": ["x" * 600]}), "超长正则应被拒绝"
    assert tl.validate_template({**base, "success_keywords": "签到成功"}), "非数组应被拒绝"
    assert tl.validate_template({**base, "submit_body": {"a": ["b"]}}), "非标量对象值应被拒绝"
    # nexusphp 需要 sign_path，yysub 不需要
    assert tl.validate_template({**base, "flow": "yysub", "sign_path": None, "key": "demo2"})


def test_resolve_fills_protocol_defaults():
    raw = {
        "key": "demo", "name": "Demo", "label": "演示", "flow": "discuz",
        "auth_type": "cookie", "auth_label": "Cookies 认证",
        "default_domain": "https://demo.example", "cookie_placeholder": "",
        "sign_path": "/sign",
    }
    tpl = tl.resolve_template(raw)
    assert tpl["sign_path"] == "/sign", "站点自身配置应覆盖协议默认值"
    assert tpl["submit_path"] == tl.DISCUZ_DEFAULTS["submit_path"], "未覆盖字段应取协议默认值"
    assert tpl["formhash_field"] == "formhash"
    # 默认值必须是深拷贝，调用方不得污染协议默认配置
    tpl["submit_body"]["qdxq"] = "mutated"
    assert tl.DISCUZ_DEFAULTS["submit_body"]["qdxq"] == "kx", "协议默认配置被污染"


def test_parse_catalog():
    valid, errors = tl.parse_catalog(json.dumps({"schema": 1, "templates": [
        {"key": "ok", "name": "OK", "label": "OK", "flow": "yysub", "auth_type": "password",
         "auth_label": "账号", "default_domain": "https://ok.example", "cookie_placeholder": ""},
    ]}))
    assert len(valid) == 1 and errors == []

    valid, errors = tl.parse_catalog(json.dumps({"schema": 1, "templates": [{"key": "bad"}]}))
    assert valid == [] and errors, "非法模板应进 errors 而非 valid"

    for bad_doc in ('[]', '{"schema": 2, "templates": []}', '{"schema": 1}'):
        try:
            tl.parse_catalog(bad_doc)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad_doc} 应抛出 ValueError")


def test_all_templates_and_dispatch():
    merged = tl.all_templates()
    for key in ("cnlang", "audiences", "acgrip", "yysub"):
        assert key in merged, f"缺少内置模板 {key}"
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


def test_local_template_library_roundtrip():
    init_db()
    raw = {
        "key": "unittest_local", "name": "本地模板", "label": "本地", "flow": "nexusphp",
        "auth_type": "cookie", "auth_label": "Cookies 认证",
        "default_domain": "https://local.example", "cookie_placeholder": "",
        "sign_path": "/attendance.php",
    }
    try:
        tl.save_template(raw)
        merged = tl.all_templates()
        assert merged["unittest_local"]["flow"] == "nexusphp"
        assert merged["unittest_local"]["done_keywords"] == tl.NEXUSPHP_DEFAULTS["done_keywords"]
        assert tl.get_template_auth_type("unittest_local") == "cookie"
    finally:
        tl.delete_template("unittest_local")
    assert "unittest_local" not in tl.all_templates()
    assert tl.delete_template("unittest_local") is False


def test_registry_fetch_and_import():
    """用本地 HTTP 服务模拟远程模板库，验证「拉取 → 导入 → 覆盖内置」整条链路。"""
    import functools
    import os
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(QuietHandler, directory=str(tl.BUILTIN_PATH.parent))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    old = os.environ.get("PIG_TEMPLATE_REGISTRY_URL")
    os.environ["PIG_TEMPLATE_REGISTRY_URL"] = f"http://127.0.0.1:{server.server_address[1]}/{tl.BUILTIN_PATH.name}"
    try:
        ok, result = tl.fetch_registry()
        assert ok, f"本地模板库拉取失败: {result['error']}"
        assert len(result["templates"]) == len(tl.builtin_catalog())

        raw = {**result["templates"][0], "label": "远程覆盖"}
        try:
            tl.save_template(raw)
            assert tl.get_template(raw["key"])["label"] == "远程覆盖", "导入的模板应覆盖内置同名模板"
        finally:
            tl.delete_template(raw["key"])
        assert tl.get_template(raw["key"])["label"] != "远程覆盖", "删除导入模板后应回退内置模板"
    finally:
        if old is None:
            os.environ.pop("PIG_TEMPLATE_REGISTRY_URL", None)
        else:
            os.environ["PIG_TEMPLATE_REGISTRY_URL"] = old
        server.shutdown()


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
