import os
import datetime
from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from pig_checkin.db import init_db, get_db, verify_password, hash_password, needs_rehash, password_error
from pig_checkin.crypto import encrypt_text
from pig_checkin.scheduler import start_scheduler, compute_next_run, submit_site, running_site_ids
from pig_checkin import templates as tpl_lib

app = FastAPI(title="猪签 - Pig Checkin")
app.add_middleware(SessionMiddleware, secret_key="pig-checkin-super-secret-key-lazy-is-best")

try:
    import importlib.metadata
    APP_VERSION = importlib.metadata.version("pig-checkin")
except Exception:
    APP_VERSION = "0.2.4"

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

@app.on_event("startup")
def startup():
    init_db()
    start_scheduler()

def session_user(request: Request) -> str | None:
    """取当前会话用户；该用户已不在库里（例如数据库被重建）就清掉会话，视为未登录。"""
    user = request.session.get("user")
    if not user:
        return None
    with get_db() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (user,)).fetchone()
    if not exists:
        request.session.clear()
        return None
    return user

def get_current_user(request: Request):
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if session_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})

@app.post("/login", response_class=HTMLResponse)
def login_action(request: Request, username: str = Form(...), password: str = Form(...)):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and verify_password(user["password"], password):
            if needs_rehash(user["password"]):
                conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), user["id"]))
                conn.commit()
            request.session["user"] = username
            return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": "用户名或密码错误"})

def get_dashboard_state():
    today = datetime.date.today().isoformat()
    with get_db() as conn:
        sites_cur = conn.execute("SELECT * FROM sites ORDER BY id ASC").fetchall()
        sites = [dict(s) for s in sites_cur]
        for s in sites:
            if not s.get("enabled"):
                s["next_run_display"] = "已关闭"
            elif s.get("next_run"):
                s["next_run_display"] = str(s["next_run"])
            else:
                s["next_run_display"] = "待计算"
            s["last_run_display"] = str(s.get("last_run")) if s.get("last_run") else "未执行"

            # 「最新状态」只认当天的结果：昨天签成功不代表今天签过了（last_run 形如 2026-10-13 09:00:00）
            if s.get("last_run") and str(s["last_run"])[:10] != today:
                s["last_status"] = "今日未执行"

            # 仅在前端下发是否已填写的状态标识，凭据脱敏置空，不在前端暴露真实明文或密文
            s["has_cookies"] = bool(s.get("cookies") and str(s["cookies"]).strip())
            s["has_username"] = bool(s.get("username") and str(s["username"]).strip())
            s["has_password"] = bool(s.get("password") and str(s["password"]).strip())
            s["has_token"] = bool(s.get("token") and str(s["token"]).strip())
            s["cookies"] = ""
            s["username"] = ""
            s["password"] = ""
            s["token"] = ""

        logs_cur = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 500").fetchall()
        logs = [dict(l) for l in logs_cur]
    return sites, logs

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

@app.post("/change-account")
def change_account(
    request: Request,
    new_username: str = Form(...),
    old_password: str = Form(...),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    user: str = Depends(get_current_user)
):
    new_username = new_username.strip()
    if not new_username:
        return JSONResponse({"ok": False, "message": "用户名不能为空"}, status_code=400)

    if new_password and new_password != confirm_password:
        return JSONResponse({"ok": False, "message": "两次输入的新密码不一致"}, status_code=400)

    if new_password.strip():
        error = password_error(new_password.strip(), new_username)
        if error:
            return JSONResponse({"ok": False, "message": error}, status_code=400)

    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (user,)).fetchone()
        if not row or not verify_password(row["password"], old_password):
            return JSONResponse({"ok": False, "message": "原密码不正确"}, status_code=400)

        if new_username != user:
            conflict = conn.execute("SELECT id FROM users WHERE username = ?", (new_username,)).fetchone()
            if conflict:
                return JSONResponse({"ok": False, "message": "新用户名已被占用"}, status_code=400)

        if new_password.strip():
            conn.execute(
                "UPDATE users SET username = ?, password = ? WHERE id = ?",
                (new_username, hash_password(new_password.strip()), row["id"])
            )
        else:
            conn.execute(
                "UPDATE users SET username = ? WHERE id = ?",
                (new_username, row["id"])
            )
        conn.commit()

    # 改完账号/密码就结束会话，强制用新凭据重新登录
    request.session.clear()
    return JSONResponse({"ok": True, "message": "账号信息修改成功，请用新凭据重新登录", "logout": True})

@app.get("/api/dashboard-data")
def api_dashboard_data(user: str = Depends(get_current_user)):
    sites, logs = get_dashboard_state()
    return {
        "ok": True,
        "current_user": user,
        "app_version": APP_VERSION,
        "sites": sites,
        "logs": logs,
        "running_ids": sorted(running_site_ids())
    }

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    sites, logs = get_dashboard_state()
    # 下拉框只列已启用模板，但已存在站点用到的模板必须保留，否则旧站点无法编辑
    all_tpl = tpl_lib.all_templates()
    selectable = tpl_lib.enabled_templates()
    for site_type in {s["site_type"] for s in sites}:
        if site_type in all_tpl:
            selectable[site_type] = all_tpl[site_type]
    return templates.TemplateResponse(request, "dashboard.html", {
        "current_user": user,
        "app_version": APP_VERSION,
        "sites": sites,
        "logs": logs,
        "site_templates": selectable,
    })

@app.post("/add-site")
def add_site(
    name: str = Form(...),
    site_type: str = Form("cnlang"),
    domain: str = Form("https://cnlang.org"),
    cookies: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    token: str = Form(""),
    schedule_type: str = Form("random"),
    schedule_time: str = Form("09:00"),
    max_retries: int = Form(3),
    retry_interval: int = Form(60),
    user: str = Depends(get_current_user)
):
    auth_type = (tpl_lib.get_template(site_type) or {}).get("auth_type", "cookie")
    initial_next_run = compute_next_run(schedule_type, schedule_time)
    enc_cookies = encrypt_text(cookies.strip())
    enc_username = encrypt_text(username.strip())
    enc_password = encrypt_text(password.strip())
    enc_token = encrypt_text(token.strip())
    with get_db() as conn:
        conn.execute("""
            INSERT INTO sites (name, site_type, domain, auth_type, cookies, username, password, token, schedule_type, schedule_time, max_retries, retry_interval, enabled, next_run, last_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, '等待签到')
        """, (name, site_type, domain, auth_type, enc_cookies, enc_username, enc_password, enc_token, schedule_type, schedule_time, max_retries, retry_interval, initial_next_run))
        conn.commit()
    return JSONResponse({"ok": True, "message": f"成功添加站点 [{name}]"})

@app.post("/edit-site")
def edit_site(
    id: int = Form(...),
    name: str = Form(...),
    site_type: str = Form("cnlang"),
    domain: str = Form("https://cnlang.org"),
    cookies: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    token: str = Form(""),
    schedule_type: str = Form("random"),
    schedule_time: str = Form("09:00"),
    max_retries: int = Form(3),
    retry_interval: int = Form(60),
    enabled: int = Form(1),
    user: str = Depends(get_current_user)
):
    auth_type = (tpl_lib.get_template(site_type) or {}).get("auth_type", "cookie")
    initial_next_run = compute_next_run(schedule_type, schedule_time)
    
    with get_db() as conn:
        old_site = conn.execute("SELECT cookies, username, password, token FROM sites WHERE id = ?", (id,)).fetchone()
        
        # 增量覆盖规则：填入新值则加密覆盖；留空/未填写则保留原有保存值
        if cookies.strip():
            enc_cookies = encrypt_text(cookies.strip())
        else:
            enc_cookies = old_site["cookies"] if old_site and old_site["cookies"] else ""

        if username.strip():
            enc_username = encrypt_text(username.strip())
        else:
            enc_username = old_site["username"] if old_site and old_site["username"] else ""

        if password.strip():
            enc_password = encrypt_text(password.strip())
        else:
            enc_password = old_site["password"] if old_site and old_site["password"] else ""

        if token.strip():
            enc_token = encrypt_text(token.strip())
        else:
            enc_token = old_site["token"] if old_site and old_site["token"] else ""

        conn.execute("""
            UPDATE sites 
            SET name = ?, site_type = ?, domain = ?, auth_type = ?, cookies = ?, username = ?, password = ?, token = ?,
                schedule_type = ?, schedule_time = ?, max_retries = ?, retry_interval = ?, enabled = ?, next_run = ?
            WHERE id = ?
        """, (name, site_type, domain, auth_type, enc_cookies, enc_username, enc_password, enc_token, schedule_type, schedule_time, max_retries, retry_interval, enabled, initial_next_run, id))
        conn.commit()
    return JSONResponse({"ok": True, "message": f"成功更新站点 [{name}] 配置"})

@app.get("/toggle-site/{site_id}")
def toggle_site(site_id: int, user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("UPDATE sites SET enabled = 1 - enabled WHERE id = ?", (site_id,))
        conn.commit()
    return JSONResponse({"ok": True, "message": "定时开关状态已更新"})

@app.get("/delete-site/{site_id}")
def delete_site(site_id: int, user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        conn.commit()
    return JSONResponse({"ok": True, "message": "站点已成功删除"})

@app.get("/api/site-logs/{site_id}")
def api_site_logs(site_id: int, user: str = Depends(get_current_user)):
    with get_db() as conn:
        site = conn.execute("SELECT id, name FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site:
            return JSONResponse({"ok": False, "message": "站点不存在"}, status_code=404)
        
        logs_cur = conn.execute(
            "SELECT * FROM logs WHERE site_id = ? OR site_name = ? ORDER BY id DESC LIMIT 500", 
            (site_id, site["name"])
        ).fetchall()
        logs = [dict(l) for l in logs_cur]

    return {
        "ok": True,
        "site_id": site_id,
        "site_name": site["name"],
        "logs": logs
    }

@app.get("/run-site/{site_id}")
def run_site(site_id: int, user: str = Depends(get_current_user)):
    """立即签到：丢进线程池后立刻返回，结果由前端轮询状态/日志呈现。

    单站签到可能跑几分钟（重试间隔 60 秒 × N 次），同步等会一直占着请求，
    而且前端每 2 秒重绘表格会把转圈的按钮整块换掉，看起来就是「转圈突然停了」。
    """
    with get_db() as conn:
        site = conn.execute("SELECT id, name FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site:
            return JSONResponse({"ok": False, "message": "站点不存在"}, status_code=404)
        name = site["name"]

    if not submit_site(site_id):
        return JSONResponse({"ok": True, "queued": False, "message": f"[{name}] 正在签到中，请稍候"})
    return JSONResponse({"ok": True, "queued": True, "message": f"[{name}] 已开始签到"})

@app.get("/run-all")
def run_all(user: str = Depends(get_current_user)):
    with get_db() as conn:
        sites = conn.execute("SELECT id FROM sites WHERE enabled = 1").fetchall()

    queued = sum(1 for site in sites if submit_site(site["id"]))
    skipped = len(sites) - queued
    message = f"已提交 {queued} 个站点签到"
    if skipped:
        message += f"，{skipped} 个正在执行中已跳过"
    return JSONResponse({"ok": True, "queued": queued, "skipped": skipped, "message": message})

@app.get("/clear-logs")
def clear_logs(user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM logs")
        conn.commit()
    return JSONResponse({"ok": True, "message": "签到日志已清空"})

# ------------------------------------------------------------------ 模板库

def _template_summary(tpl: dict) -> dict:
    return {
        "key": tpl["key"],
        "name": tpl["name"],
        "flow": tpl["flow"],
        "default_domain": tpl["default_domain"],
        "auth_type": tpl.get("auth_type", "cookie"),
        "auth_label": tpl.get("auth_label", ""),
        "cookie_placeholder": tpl.get("cookie_placeholder", ""),
    }

@app.get("/api/template-library")
def api_template_library(user: str = Depends(get_current_user)):
    all_tpl = tpl_lib.all_templates()
    enabled = tpl_lib.enabled_templates()
    usage = tpl_lib.usage_counts()
    return {
        "ok": True,
        "all": [
            {**_template_summary(tpl), "enabled": key in enabled, "in_use": usage.get(key, 0)}
            for key, tpl in sorted(all_tpl.items(), key=lambda kv: kv[1]["name"])
        ],
        "enabled": [
            {**_template_summary(tpl), "in_use": usage.get(key, 0)}
            for key, tpl in sorted(enabled.items(), key=lambda kv: kv[1]["name"])
        ],
    }

@app.post("/api/template-library/toggle")
def api_template_toggle(key: str = Form(...), enabled: int = Form(...), user: str = Depends(get_current_user)):
    error = tpl_lib.set_template_enabled(key, bool(enabled))
    if error:
        return JSONResponse({"ok": False, "message": error}, status_code=400)
    state = "启用" if enabled else "停用"
    return JSONResponse({"ok": True, "message": f"已{state}模板 [{tpl_lib.get_template(key)['name']}]"})
