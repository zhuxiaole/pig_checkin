from pig_checkin.checkin import parse_cookies_str, FLOWS
from pig_checkin.templates_config import SITE_TEMPLATES, get_template_auth_type
from pig_checkin.scheduler import compute_initial_next_run, compute_next_day_run
from pig_checkin.db import init_db, get_db
from pig_checkin.app import APP_VERSION

def test_all():
    assert parse_cookies_str("a=1; b=2; c=val=ue") == {"a": "1", "b": "2", "c": "val=ue"}
    assert parse_cookies_str("") == {}

    for key, tpl in SITE_TEMPLATES.items():
        assert tpl.get("flow") in FLOWS, f"{key} 未声明有效 flow"
        assert tpl.get("label"), f"{key} 缺少 label"
        assert tpl["default_domain"].startswith("http"), f"{key} default_domain 非法"
        if tpl["flow"] in ("discuz", "nexusphp"):
            assert tpl.get("sign_path", "").startswith("/"), f"{key} 缺少 sign_path"

    t1 = compute_initial_next_run("fixed", "12:00")
    t2 = compute_next_day_run("random", "09:00")
    assert len(t1) == 19 and len(t2) == 19

    init_db()
    with get_db() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1

    assert bool(APP_VERSION)
    assert get_template_auth_type("yysub") == "password"
    assert get_template_auth_type("cnlang") == "cookie"

    # 分发链路：execute_checkin_once 必须按 tpl["flow"] 调用对应协议实现
    import pig_checkin.checkin as ck
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

if __name__ == "__main__":
    test_all()
    print("OK")
