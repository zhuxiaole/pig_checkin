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
    for key in ("cnlang", "audiences", "acgrip", "yyets", "dstudio"):
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
        return ('<html><head><style>.erqd-checkin-btn2 { background-color: #4082b9; }</style></head>'
                '<body><script>var discuz_uid = %s;</script>'
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


def test_discuz_dsu_paulsign_path_unchanged():
    """dsu_paulsign 站点（cnlang 等）：提交体仍是旧的 5 个字段，应答走关键字判定。"""
    import requests
    from pig_checkin.checkin import checkin_discuz_dsu_paulsign

    tpl = tl.get_template("cnlang")
    assert tpl["greeting_field"] == "todaysay", "老站点必须仍带 todaysay"
    html = ('<html><body><script>var discuz_uid = 22775;</script>'
            '<a href="home.php?mod=spacecp">zhuxiaole</a>'
            '<input type="hidden" name="formhash" value="a1b2c3d4">'
            '<form id="qiandao"><input name="qdxq" value="kx"></form></body></html>')
    reply = "<root><![CDATA[<script>showmessage('签到成功');</script>]]></root>"

    class Resp:
        def __init__(self, text):
            self.status_code = 200
            self.text = text
            self.content = text.encode("utf-8")
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

        def json(self):
            raise ValueError("not json")

    sent = {}
    orig_get, orig_post = requests.Session.get, requests.Session.post
    try:
        requests.Session.get = lambda self, url, **kw: Resp(html)
        requests.Session.post = lambda self, url, **kw: sent.update(kw.get("data") or {}) or Resp(reply)
        ok, msg = checkin_discuz_dsu_paulsign({"domain": "https://cnlang.org", "cookies": "x=y"}, tpl)
    finally:
        requests.Session.get, requests.Session.post = orig_get, orig_post

    assert ok is True and "签到成功" in msg
    assert set(sent) == {"qdxq", "qdmode", "fastreply", "formhash", "todaysay"}, sent
    assert sent["formhash"] == "a1b2c3d4" and sent["qdxq"] == "kx"


def test_discuz_token_in_url_placeholder():
    """多看（zqlj_sign）：令牌在 URL 上而不是表单字段，且页面里还有个登录表单的 formhash 干扰。"""
    import requests
    from pig_checkin.checkin import checkin_discuz_dsu_paulsign

    tpl = tl.get_template("duokan")
    assert tpl["submit_path"].endswith("{formhash}")

    html = ('<html><body><script>var discuz_uid = 573515;</script>'
            '<a href="home.php?mod=spacecp" class="vwmy"><a>猪小乐</a>'
            '<input type="hidden" name="formhash" value="8b532b5c">'
            '<div class="bm signbtn cl"><a href="sign.php?sign=ea2fcc50" class="btna">立即打卡</a></div>'
            '</body></html>')
    reply = '<html><body><div class="showmsg">打卡成功，获得 10 铜板</div></body></html>'

    class Resp:
        def __init__(self, text):
            self.status_code = 200
            self.text = text
            self.content = text.encode("utf-8")
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

        def json(self):
            raise ValueError("not json")

    sent = {}
    orig_get, orig_post = requests.Session.get, requests.Session.post
    try:
        requests.Session.get = lambda self, url, **kw: Resp(html)

        def fake_post(self, url, **kw):
            sent["url"] = url
            sent["data"] = kw.get("data")
            return Resp(reply)

        requests.Session.post = fake_post
        ok, msg = checkin_discuz_dsu_paulsign({"domain": "https://duokan.club", "cookies": "x=y"}, tpl)
    finally:
        requests.Session.get, requests.Session.post = orig_get, orig_post

    assert ok is True and "打卡成功" in msg
    assert sent["url"].endswith("/sign.php?sign=ea2fcc50"), sent["url"]
    assert "8b532b5c" not in sent["url"], "用了登录表单的 formhash，而不是打卡令牌"


def test_token_auth_type_encrypted_at_rest():
    """密钥认证：库里必须是密文，前端只下发 has_token，执行时才解密。"""
    from pig_checkin.app import add_site, get_dashboard_state
    from pig_checkin.checkin import execute_checkin_once
    from pig_checkin.crypto import decrypt_text, is_encrypted

    key = "pgk_" + "A" * 43
    with get_db() as conn:
        conn.execute("DELETE FROM sites WHERE name = '__unittest_token__'")
        conn.commit()
    try:
        add_site(name="__unittest_token__", site_type="rousi", domain="https://rousi.pro",
                 cookies="", username="", password="", token=key,
                 schedule_type="random", schedule_time="09:00",
                 max_retries=1, retry_interval=1, user="admin")
        with get_db() as conn:
            row = dict(conn.execute("SELECT * FROM sites WHERE name = '__unittest_token__'").fetchone())

        assert row["auth_type"] == "token"
        assert row["token"] and key not in row["token"], "密钥不能明文落库"
        assert is_encrypted(row["token"])
        assert decrypt_text(row["token"]) == key, "加解密要能还原"

        masked = [s for s in get_dashboard_state()[0] if s["id"] == row["id"]][0]
        assert masked["token"] == "" and masked["token"] is not None
        assert masked["has_token"] is True

        # 执行时用解密后的密钥，凭据缺失要明确报错而不是发空请求
        import requests
        sent = {}
        orig = requests.Session.post
        try:
            class Resp:
                status_code = 200

                @staticmethod
                def json():
                    return {"code": 0, "message": "success",
                            "data": {"reward": 100, "current_streak": 1, "total_days": 81}}

            def fake_post(self, url, **kw):
                sent["auth"] = self.headers.get("Authorization")
                return Resp()

            requests.Session.post = fake_post
            ok, msg = execute_checkin_once(dict(row))
        finally:
            requests.Session.post = orig

        assert ok is True and "获得 100 魔力" in msg and "累计 81 天" in msg
        assert sent["auth"] == f"Bearer {key}", "要用解密后的密钥做 Bearer 认证"

        ok, msg = execute_checkin_once({**dict(row), "token": ""})
        assert ok is False and "未配置密钥" in msg
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM sites WHERE name = '__unittest_token__'")
            conn.commit()


def test_peergo_flow_response_branches():
    """PeerGo 接口四种应答：成功 / 今天已签到 / 密钥失效 / 权限不足。"""
    import requests
    from pig_checkin.checkin import checkin_peergo

    tpl = tl.get_template("rousi")
    assert tpl["auth_type"] == "token" and tpl["claim_path"] == "/api/points/attendance"

    class Resp:
        def __init__(self, payload, status=200):
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

    orig = requests.Session.post
    try:
        seen = {}

        def fake_post(self, url, **kw):
            seen["url"] = url
            seen["json"] = kw.get("json")
            return Resp(seen["reply"])

        requests.Session.post = fake_post
        site = {"domain": "https://rousi.pro", "token": "pgk_x"}

        seen["reply"] = {"code": 0, "message": "success",
                         "data": {"reward": 100, "current_streak": 3, "total_days": 81}}
        ok, msg = checkin_peergo(site, tpl)
        assert ok is True and "获得 100 魔力" in msg and "连续 3 天" in msg
        assert seen["url"].endswith("/api/points/attendance") and seen["json"] == {}

        seen["reply"] = {"code": 1, "message": "今天已签到"}
        ok, msg = checkin_peergo(site, tpl)
        assert ok is True and "今日已签到" in msg

        seen["reply"] = {"code": 401, "message": "API Key 无效或已撤销"}
        ok, msg = checkin_peergo(site, tpl)
        assert ok is False and "密钥无效" in msg

        seen["reply"] = {"code": 403, "message": "当前账号没有执行该操作的权限"}
        ok, msg = checkin_peergo(site, tpl)
        assert ok is False and "attendance:claim" in msg
    finally:
        requests.Session.post = orig


def test_run_site_is_async_and_deduped():
    """回归：立即签到不能再同步阻塞请求，且同一站点并发只会跑一次。"""
    import time

    from pig_checkin import scheduler as sch
    from pig_checkin.app import api_dashboard_data, run_site

    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO sites (name, site_type, domain, auth_type, enabled) VALUES (?, ?, ?, ?, 1)",
            ("__unittest_async__", "cnlang", "https://cnlang.org", "cookie"),
        )
        site_id = cur.lastrowid
        conn.commit()

    calls = []
    orig = sch.execute_checkin

    def fake_checkin(site_dict):
        calls.append(site_dict.get("id"))
        time.sleep(0.4)
        return True, "ok"

    sch.execute_checkin = fake_checkin
    try:
        started = time.time()
        first = json.loads(run_site(site_id, user="admin").body)
        elapsed = time.time() - started

        assert elapsed < 0.3, f"立即签到仍然同步阻塞了 {elapsed:.2f}s"
        assert first["ok"] is True and first["queued"] is True, first
        assert site_id in api_dashboard_data(user="admin")["running_ids"], "前端拿不到运行中站点，转圈态就没了"

        second = json.loads(run_site(site_id, user="admin").body)
        assert second["queued"] is False and "正在签到中" in second["message"], second
        assert len(calls) == 1, f"同一站点被重复提交了 {len(calls)} 次"

        deadline = time.time() + 5
        while site_id in sch.running_site_ids() and time.time() < deadline:
            time.sleep(0.1)
        assert site_id not in sch.running_site_ids(), "任务结束后没从运行集合里摘掉"
        assert len(calls) == 1
    finally:
        sch.execute_checkin = orig
        with get_db() as conn:
            conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
            conn.commit()


def test_nexusphp_already_signed_if_absent():
    """BTSchool：领取入口当天用完就不再渲染，页面没有入口 = 今日已领取；有入口但没成功文案 = 失败。"""
    import requests
    from pig_checkin.checkin import checkin_nexusphp

    tpl = tl.get_template("btschool")
    assert tpl["already_signed_if_absent"] == ["action=addbonus"]
    assert tpl["sign_path"] == "/index.php?action=addbonus"

    def page(inner: str) -> str:
        return ('<html><head><title>BTSCHOOL :: 首页</title></head><body>'
                '<a href="userdetails.php?id=134228"><b>zhuxiaole</b></a>' + inner + "</body></html>")

    JUST_CLAIMED = page("<table><tr><td style='background: green'><b><a href=\"index.php\">"
                        "<font color=\"white\">今日签到，获得42个魔力值</font></a></b></td></tr></table>")
    ALREADY = page('<font class="color_bonus">魔力值 </font>[<a href="mybonus.php">使用</a>]: 184,873.6')
    NOT_YET = page('<a href="index.php?action=addbonus">签到</a>')

    class Resp:
        def __init__(self, text):
            self.status_code = 200
            self.text = text
            self.content = text.encode("utf-8")
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

    site = {"domain": "https://pt.btschool.club", "cookies": "c_secure_uid=1"}
    orig = requests.Session.get
    try:
        requests.Session.get = lambda self, url, **kw: Resp(JUST_CLAIMED)
        ok, msg = checkin_nexusphp(site, tpl)
        assert ok is True and "42个魔力值" in msg, msg

        requests.Session.get = lambda self, url, **kw: Resp(ALREADY)
        ok, msg = checkin_nexusphp(site, tpl)
        assert ok is True and "今日已签到" in msg, msg

        # 入口还在（说明今天没领到）时不能被「入口消失」逻辑误判成成功
        requests.Session.get = lambda self, url, **kw: Resp(NOT_YET)
        ok, msg = checkin_nexusphp(site, tpl)
        assert ok is False, msg
    finally:
        requests.Session.get = orig


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


def test_cookiecloud_crypto_and_sync():
    """CookieCloud: 端对端解密、域名聚合匹配写入、配置密码加密落库。"""
    import base64
    import hashlib
    import os
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    from pig_checkin.cookiecloud import (
        evp_bytes_to_key,
        decrypt_cookiecloud_data,
        save_cookiecloud_config,
        get_cookiecloud_config,
        sync_cookies_to_sites,
        _match_domain_cookies
    )
    from pig_checkin.db import get_setting
    from pig_checkin.crypto import decrypt_text, is_encrypted

    # 1. 模拟生成端对端加密数据
    uuid = "test-uuid-888"
    password = "secret-cookiecloud-password"
    salt = os.urandom(8)
    key_password = hashlib.md5(f"{uuid}-{password}".encode("utf-8")).hexdigest()[:16].encode("utf-8")
    key, iv = evp_bytes_to_key(key_password, salt, 32, 16)

    raw_payload = {
        "cookie_data": {
            "audiences.me": [
                {"name": "c_secure_uid", "value": "uid_888"},
                {"name": "c_secure_pass", "value": "pass_999"}
            ],
            ".audiences.me": [
                {"name": "cf_clearance", "value": "cf_token_xxx"}
            ],
            "unrelated.com": [
                {"name": "foo", "value": "bar"}
            ]
        }
    }
    plaintext = json.dumps(raw_payload).encode("utf-8")
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len]) * pad_len
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    enc = cipher.encryptor()
    ciphertext = enc.update(padded) + enc.finalize()
    enc_b64 = base64.b64encode(b"Salted__" + salt + ciphertext).decode("utf-8")

    # 2. 解密验证与密码错误测试
    decrypted = decrypt_cookiecloud_data(enc_b64, uuid, password)
    assert decrypted == raw_payload
    try:
        decrypt_cookiecloud_data(enc_b64, uuid, "wrong-password")
        assert False, "错误密码应当解密失败"
    except ValueError:
        pass

    # 3. 域名匹配合并
    matched = _match_domain_cookies("audiences.me", decrypted["cookie_data"])
    assert matched["c_secure_uid"] == "uid_888"
    assert matched["c_secure_pass"] == "pass_999"
    assert matched["cf_clearance"] == "cf_token_xxx"
    assert "foo" not in matched

    # 4. 本地站点数据自动同步更新（必须加密落库）
    with get_db() as conn:
        conn.execute("DELETE FROM sites WHERE name = '__unittest_cc__'")
        cur = conn.execute(
            "INSERT INTO sites (name, domain, auth_type, site_type, cookies) VALUES (?, ?, 'cookie', 'audiences', '')",
            ("__unittest_cc__", "https://audiences.me/attendance.php")
        )
        test_site_id = cur.lastrowid
        conn.commit()

    try:
        synced = sync_cookies_to_sites(decrypted["cookie_data"])
        assert any(s["id"] == test_site_id for s in synced)
        with get_db() as conn:
            site_row = conn.execute("SELECT cookies FROM sites WHERE id = ?", (test_site_id,)).fetchone()
            assert is_encrypted(site_row["cookies"]), "同步后的 cookies 必须加密落库"
            plain_cookies = decrypt_text(site_row["cookies"])
            assert "c_secure_uid=uid_888" in plain_cookies
            assert "cf_clearance=cf_token_xxx" in plain_cookies
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM sites WHERE id = ?", (test_site_id,))
            conn.commit()

    # 5. 配置加密保存与读取（包含同步间隔）
    save_cookiecloud_config("https://cc.example.org", uuid, password, True, sync_interval=45)
    raw_db_pw = get_setting("cookiecloud_password")
    assert raw_db_pw and password not in raw_db_pw, "密码不能明文落库"
    assert is_encrypted(raw_db_pw), "密码必须用 Fernet 加密"

    cfg = get_cookiecloud_config()
    assert cfg["url"] == "https://cc.example.org"
    assert cfg["uuid"] == uuid
    assert cfg["password"] == password
    assert cfg["auto_sync"] is True
    assert cfg["sync_interval"] == 45

    # 6. 单域名提取：get_cookies_for_domain
    import requests
    orig_get = requests.get
    try:
        class FakeResp:
            status_code = 200
            def json(self):
                return {"encrypted": enc_b64}
        requests.get = lambda url, **kw: FakeResp()
        from pig_checkin.cookiecloud import get_cookies_for_domain
        from pig_checkin.app import api_get_site_cookie

        ok, cstr, meta = get_cookies_for_domain("https://audiences.me/attendance.php")
        assert ok is True and "c_secure_uid=uid_888" in cstr and "cf_clearance=cf_token_xxx" in cstr
        assert meta["count"] == 3

        # 未匹配到的域名返回明确提示
        ok, msg, _ = get_cookies_for_domain("https://not-exists-domain.com")
        assert ok is False and "未找到" in msg

        # API 端点验证
        resp = api_get_site_cookie("https://audiences.me", user="admin")
        assert resp["ok"] is True and "cf_clearance=cf_token_xxx" in resp["cookies"]
    finally:
        requests.get = orig_get


def test_solve_with_zendriver_and_xvfb():
    """回归：solve_with_zendriver 支持 Xvfb 虚拟屏幕判定与参数绑定。"""
    from unittest.mock import patch, AsyncMock
    from pig_checkin.checkin import solve_with_zendriver, ensure_xvfb

    called = {}
    async def mock_start(**kwargs):
        called.update(kwargs)
        mock_b = AsyncMock()
        mock_p = AsyncMock()
        mock_p.get_content.return_value = "<html><body>attendance-card--done</body></html>"
        mock_b.get.return_value = mock_p
        mock_b.cookies.get_all.return_value = []
        mock_b.cookies.set_all = AsyncMock()
        mock_b.stop = AsyncMock()
        return mock_b

    with patch("zendriver.start", side_effect=mock_start), \
         patch("pig_checkin.checkin.ensure_xvfb", return_value=True), \
         patch("pig_checkin.checkin.find_browser_executable", return_value="/usr/bin/chromium"):
        ok, content, _ = solve_with_zendriver("https://audiences.me/attendance.php", {"uid": "1"})
        assert ok is True
        assert called["browser_executable_path"] == "/usr/bin/chromium"
        # 当具有 Xvfb 时，默认有头模式跑在虚拟桌面里
        assert called["headless"] is False
        assert called["sandbox"] is False


def test_smart_cookie_merge_and_persistence():
    """过盾后自动增量更新站点 Cookies，且 CookieCloud 定时同步时智能保留本地已有 cf_clearance。"""
    from pig_checkin.checkin import _persist_site_cookies_merge, parse_cookies_str
    from pig_checkin.cookiecloud import sync_cookies_to_sites
    from pig_checkin.crypto import encrypt_text, decrypt_text

    # 1. 测试 _persist_site_cookies_merge
    with get_db() as conn:
        conn.execute("DELETE FROM sites WHERE name = '__unittest_merge__'")
        cur = conn.execute(
            "INSERT INTO sites (name, domain, auth_type, site_type, cookies) VALUES (?, ?, 'cookie', 'audiences', ?)",
            ("__unittest_merge__", "https://audiences.me/attendance.php", encrypt_text("c_secure_uid=123; c_secure_pass=old_pwd"))
        )
        site_id = cur.lastrowid
        conn.commit()

    try:
        # 模拟过盾拿到新 cf_clearance 与更新的 uid
        _persist_site_cookies_merge(site_id, {"cf_clearance": "fresh_token_999", "c_secure_uid": "123"})
        with get_db() as conn:
            row = conn.execute("SELECT cookies FROM sites WHERE id = ?", (site_id,)).fetchone()
            curr = parse_cookies_str(decrypt_text(row["cookies"]))
            assert curr["cf_clearance"] == "fresh_token_999", "过盾后新 cf_clearance 必须已持久化"
            assert curr["c_secure_pass"] == "old_pwd", "原有登录 Cookie 必须完整保留"

        # 2. 模拟 CookieCloud 定时拉取（云端无 cf_clearance，只有新密码凭据）
        cloud_cookie_map = {
            "audiences.me": [
                {"name": "c_secure_uid", "value": "123"},
                {"name": "c_secure_pass", "value": "new_cloud_pwd"}
            ]
        }
        sync_cookies_to_sites(cloud_cookie_map)

        with get_db() as conn:
            row = conn.execute("SELECT cookies FROM sites WHERE id = ?", (site_id,)).fetchone()
            after = parse_cookies_str(decrypt_text(row["cookies"]))
            assert after["c_secure_pass"] == "new_cloud_pwd", "云端最新密码凭据必须已同步覆盖"
            assert after["cf_clearance"] == "fresh_token_999", "本地已过盾的 cf_clearance 绝不能被反向冲掉！"
    finally:
        with get_db() as conn:
            conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
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
