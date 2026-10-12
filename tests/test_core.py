from pig_checkin.checkin import parse_cookies_str, SITE_HANDLERS
from pig_checkin.templates_config import SITE_TEMPLATES, get_template_auth_type
from pig_checkin.scheduler import compute_initial_next_run, compute_next_day_run
from pig_checkin.db import init_db, get_db
from pig_checkin.app import APP_VERSION

def test_all():
    assert parse_cookies_str("a=1; b=2; c=val=ue") == {"a": "1", "b": "2", "c": "val=ue"}
    assert parse_cookies_str("") == {}

    for t in SITE_TEMPLATES:
        assert t in SITE_HANDLERS, f"Missing handler for {t}"

    t1 = compute_initial_next_run("fixed", "12:00")
    t2 = compute_next_day_run("random", "09:00")
    assert len(t1) == 19 and len(t2) == 19

    init_db()
    with get_db() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1

    assert bool(APP_VERSION)
    assert get_template_auth_type("yysub") == "password"
    assert get_template_auth_type("cnlang") == "cookie"

if __name__ == "__main__":
    test_all()
    print("OK")
