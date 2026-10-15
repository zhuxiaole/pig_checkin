import json
import secrets

import yaml

import pig_checkin.checkin as ck
from pig_checkin.checkin import parse_cookies_str, FLOWS
from pig_checkin import templates as tl
from pig_checkin.scheduler import compute_next_run
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


class _Req:  # dashboard 只需要 session 与 query_params
    session = {"user": "admin"}
    query_params = {}


def _add_site_options() -> set[str]:
    """解析「添加网站」弹窗里模板下拉框的可选 key。"""
    from pig_checkin import app as appmod

    html = appmod.dashboard(_Req()).body.decode("utf-8")
    select = html.split('id="add_site_type"', 1)[1].split("</select>", 1)[0]
    return {seg.split('"')[0] for seg in select.split('<option value="')[1:]}


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
    key = "unittest_free"
    fake = {**BASE, "key": key}
    assert tl.validate_template(fake) == []
    tl._catalog_cache = tl.builtin_catalog() + [fake]  # 注入一个无站点引用的模板
    try:
        assert key not in tl.enabled_templates(), "默认不启用任何模板"
        assert tl.set_template_enabled(key, True) is None
        assert key in tl.enabled_templates(), "启用后应出现在可选模板中"
        assert tl.set_template_enabled(key, False) is None, "未被使用的模板应可停用"
        assert key not in tl.enabled_templates(), "停用后不应出现在可选模板中"
        assert tl.get_template(key) is not None, "停用不应从模板库移除"
    finally:
        tl.set_template_enabled(key, False)
        tl._catalog_cache = [t for t in tl._catalog_cache if t["key"] != key]
    assert tl.set_template_enabled("no-such-key", True), "不存在的模板应返回错误说明"


def test_cannot_disable_in_use_template():
    """造一个引用某模板的临时站点，验证它拦住停用，并在结束后清理干净。"""
    key = "cnlang"
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO sites (name, site_type, domain, auth_type, cookies) VALUES (?, ?, ?, ?, ?)",
            ("__unittest__", key, "https://cnlang.org", "cookie", ""),
        )
        site_id = cur.lastrowid
        conn.commit()

    try:
        assert tl.set_template_enabled(key, True) is None
        error = tl.set_template_enabled(key, False)
        assert error, f"在用模板 [{key}] 必须拒绝停用"
        assert key in tl.enabled_templates(), "被拒绝后不应真的被停用"
        assert key in _add_site_options(), "在用模板必须留在编辑下拉框里"
    finally:
        # 先删站点再停用，否则会被"在用不可停用"的守卫挡住（顺序反了会留下脏状态）
        with get_db() as conn:
            conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
            conn.commit()
        tl.set_template_enabled(key, False)


def test_dropdown_only_lists_enabled_or_in_use():
    """默认不启用任何模板时，下拉框只保留已存在站点用到的模板。"""
    with get_db() as conn:
        in_use = {r[0] for r in conn.execute("SELECT DISTINCT site_type FROM sites").fetchall()}
    assert _add_site_options() == in_use | tl.enabled_keys()


def test_all_templates_and_dispatch():
    merged = tl.all_templates()
    for key in ("cnlang", "audiences", "acgrip", "yyets"):
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


def test_nexusphp_attendance_patterns():
    from pig_checkin.checkin import checkin_nexusphp
    tpl = tl.get_template("hhanclub")
    html = """
    <html><head><title>HHCLUB</title></head><body>
    <a href="userdetails.php?id=22775"><b>zhuxiaole</b></a>
    <p class="register-now-info register-info">这是您的第460次签到，已连续签到5天，本次签到获得30个憨豆。你目前拥有补签卡5张。</p>
    </body></html>
    """
    import requests
    orig = requests.Session.get
    try:
        class Resp:
            status_code = 200
            text = html
        requests.Session.get = lambda self, url, **kw: Resp()
        ok, msg = checkin_nexusphp({"domain": "https://hhanclub.net", "cookies": "c_secure_uid=1; c_secure_pass=x"}, tpl)
        assert ok is True
        assert "这是您的第460次签到" in msg
        assert "30个憨豆" in msg
    finally:
        requests.Session.get = orig


def test_discuz_erling_qd_plugin_flow():
    """恩山（erling_qd 插件）：未登录/已签到/正常签到三条路径 + AJAX JSON 应答。"""
    import requests
    from pig_checkin.checkin import checkin_discuz_dsu_paulsign

    tpl = tl.get_template("right")
    assert tpl["sign_path"] == "/plugin.php?id=erling_qd:sign_in"
    assert tpl["greeting_field"] == "", "签到体里不该混进 todaysay"

    def page(uid, extra=""):
        return ('<html><body><script>var discuz_uid = %s;</script>'
                '<span class="erqd-nickname">zhuxiaole</span>%s</body></html>') % (uid, extra)

    GUEST = '<html><body><script>var discuz_uid = 0;</script><div class="login-prompt">您需要登录后才能使用签到功能</div></body></html>'
    SIGNED = page(123, '<button id="signin-btn" class="erqd-checkin-btn erqd-checkin-btn2" disabled>已签到</button>')
    READY = page(123, '<button id="signin-btn" class="erqd-checkin-btn">立即签到</button>'
                      "<script>var FORMHASH = 'a1b2c3d4';</script>")

    class Resp:
        def __init__(self, text, status=200):
            self.status_code = status
            self.text = text
            self.content = text.encode("utf-8")
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

        def json(self):
            return __import__("json").loads(self.text)

    sent = {}
    orig_get, orig_post = requests.Session.get, requests.Session.post
    try:
        def fake_get(self, url, **kw):
            return Resp(sent.get("page", GUEST))

        def fake_post(self, url, **kw):
            sent["url"] = url
            sent["data"] = kw.get("data")
            return Resp(sent.get("reply", '{"success":true,"message":"签到成功，获得 10 个积分"}'))

        requests.Session.get = fake_get
        requests.Session.post = fake_post
        site = {"domain": "https://www.right.com.cn/forum", "cookies": "rHEX_2132_saltkey=x"}

        sent["page"] = GUEST
        assert checkin_discuz_dsu_paulsign(site, tpl) == (False, "Cookies 已失效或未登录，请更新 Cookies")

        sent["page"] = SIGNED
        ok, msg = checkin_discuz_dsu_paulsign(site, tpl)
        assert ok is True and "今日已签到" in msg
        assert "url" not in sent, "已签到时不该再提交签到请求"

        sent["page"] = READY
        ok, msg = checkin_discuz_dsu_paulsign(site, tpl)
        assert ok is True and "签到成功" in msg and "10 个积分" in msg
        assert sent["url"].endswith("/plugin.php?id=erling_qd:action&action=sign")
        assert sent["data"] == {"formhash": "a1b2c3d4"}, sent["data"]

        sent["reply"] = '{"success":false,"message":"签到失败，请稍后再试"}'
        ok, msg = checkin_discuz_dsu_paulsign(site, tpl)
        assert ok is False and "签到失败" in msg
    finally:
        requests.Session.get, requests.Session.post = orig_get, orig_post


def test_latest_status_only_counts_today():
    """「最新状态」只显示当天的结果，昨天的成功不能冒充今天。"""
    import datetime

    from pig_checkin.app import get_dashboard_state

    today = datetime.date.today()
    rows = {
        "昨天失败": ((today - datetime.timedelta(days=1)).strftime("%Y-%m-%d 09:00:00"), "签到失败"),
        "今天成功": (today.strftime("%Y-%m-%d 09:00:00"), "签到成功"),
        "从未执行": (None, "等待签到"),
    }
    ids = []
    with get_db() as conn:
        for name, (last_run, status) in rows.items():
            cur = conn.execute(
                "INSERT INTO sites (name, site_type, domain, auth_type, last_run, last_status) VALUES (?, ?, ?, ?, ?, ?)",
                (f"__unittest_{name}__", "cnlang", "https://cnlang.org", "cookie", last_run, status),
            )
            ids.append(cur.lastrowid)
        conn.commit()

    try:
        sites, _ = get_dashboard_state()
        got = {s["name"]: s["last_status"] for s in sites if s["id"] in ids}
        assert got["__unittest_昨天失败__"] == "今日未执行", "昨天跑的不能算今天的状态"
        assert got["__unittest_今天成功__"] == "签到成功", "今天跑过的照常显示"
        assert got["__unittest_从未执行__"] == "等待签到", "从没跑过保持原样"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM sites WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
            conn.commit()


def test_change_account_logs_out_and_changes_password():
    """回归：改账号/密码要同时改库并结束会话（曾被误删 request 参数打成 500）。"""
    from pig_checkin.app import change_account
    from pig_checkin.db import hash_password, verify_password

    old_name, new_name = "__unittest_a__", "__unittest_b__"
    old_pw, new_pw = "pw-old-123", "pw-new-456"
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE username IN (?, ?)", (old_name, new_name))
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (old_name, hash_password(old_pw)))
        conn.commit()

    req = _Req()
    req.session = {"user": old_name}  # 实例属性覆盖类属性，避免污染其它用例
    try:
        resp = change_account(request=req, new_username=new_name, old_password=old_pw,
                              new_password=new_pw, confirm_password=new_pw, user=old_name)
        assert resp.status_code == 200, resp.body
        assert json.loads(resp.body)["logout"] is True, "要告诉前端去重新登录"
        assert req.session == {}, "改完凭据必须结束会话"
        with get_db() as conn:
            row = conn.execute("SELECT password FROM users WHERE username = ?", (new_name,)).fetchone()
        assert row and verify_password(row["password"], new_pw), "新密码要生效"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM users WHERE username IN (?, ?)", (old_name, new_name))
            conn.commit()


def test_session_invalidated_when_user_vanished():
    """回归：数据库被重建后，旧 cookie 里的用户名不该继续算已登录。"""
    from pig_checkin.app import dashboard, session_user

    req = _Req()
    req.session = {"user": "__ghost__"}
    assert session_user(req) is None, "库里查不到的用户应视为未登录"
    assert req.session == {}, "并顺手清掉会话"

    req2 = _Req()
    req2.session = {"user": "__ghost__"}
    resp = dashboard(req2)
    assert resp.status_code == 303, "应重定向而不是渲染控制台"
    assert resp.headers["location"] == "/login"


def test_password_policy():
    from pig_checkin.db import password_error

    ok = ["Abcdefg1", "P@ssw0rd!", "aA1&b=c#d+e%f?g", "Zx9!@#$%^&*", "a" * 62 + "b1"]
    for pw in ok:
        assert password_error(pw, "zhuxiaole") is None, f"{pw!r} 应通过"

    bad = {
        "Abcdefg": "只有 7 位",
        "1abcdefgh": "以数字开头",
        "!abcdefg1": "以符号开头",
        "  Abcdefg1": "以空格开头",
        "abc def1": "含空格",
        "密码abc123": "含中文",
        "a1😀😀😀😀😀😀": "含 emoji",
        "ａｂｃｄｅｆｇ１": "全角字符",
        "abcdefgh": "无数字",
        "admin123": "常见弱密码",
        "a" * 63 + "b1": "超长",
        "zhuxiaole1": "含用户名",
        "Abcdefg1。": "含全角句号",
    }
    for pw, why in bad.items():
        assert password_error(pw, "zhuxiaole"), f"{pw!r} 应被拒绝（{why}）"


def test_password_hash_scrypt_and_legacy():
    import hashlib

    from pig_checkin.db import hash_password, needs_rehash, verify_password

    stored = hash_password("GoodPass123")
    assert stored.startswith("scrypt$"), "新哈希应是 scrypt 格式"
    assert verify_password(stored, "GoodPass123")
    assert not verify_password(stored, "GoodPass124")
    assert not verify_password(stored, "")
    assert not verify_password("scrypt$bad$data", "GoodPass123"), "损坏的存储串不得放行"
    assert not needs_rehash(stored)

    # 旧格式仍可验证，并被标记需要升级
    legacy_salt = "a" * 32
    legacy = f"{legacy_salt}:{hashlib.sha256((legacy_salt + 'oldpass123').encode()).hexdigest()}"
    assert verify_password(legacy, "oldpass123"), "旧 sha256 密码必须还能登录"
    assert not verify_password(legacy, "oldpass124")
    assert needs_rehash(legacy), "旧格式应被标记为待升级"


def test_login_upgrades_legacy_hash():
    import hashlib

    from pig_checkin.app import login_action
    from pig_checkin.db import verify_password

    name, password = "__unittest_legacy__", "oldpass123"
    salt = secrets.token_hex(16)
    legacy = f"{salt}:{hashlib.sha256((salt + password).encode()).hexdigest()}"
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE username = ?", (name,))
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (name, legacy))
        conn.commit()

    req = _Req()
    req.session = {}
    try:
        resp = login_action(request=req, username=name, password=password)
        assert resp.status_code == 303, "旧密码应能登录"
        with get_db() as conn:
            stored = conn.execute("SELECT password FROM users WHERE username = ?", (name,)).fetchone()[0]
        assert stored.startswith("scrypt$"), "登录成功后应自动升级到 scrypt"
        assert verify_password(stored, password), "升级后仍能用原密码登录"
        assert req.session["user"] == name
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM users WHERE username = ?", (name,))
            conn.commit()


def test_scheduler_and_db():
    assert len(compute_next_run("fixed", "12:00")) == 19
    assert len(compute_next_run("random", "09:00")) == 19
    assert len(compute_next_run("random", "09:00", next_day=True)) == 19
    with get_db() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert bool(APP_VERSION)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("OK")
