import os
import datetime
from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from pig_checkin.db import init_db, get_db, verify_password, hash_password
from pig_checkin.crypto import encrypt_text, decrypt_text
from pig_checkin.checkin import execute_checkin
from pig_checkin.scheduler import start_scheduler, compute_initial_next_run, compute_next_day_run
from pig_checkin import templates as tpl_lib

app = FastAPI(title="Pig Checkin Service")
app.add_middleware(SessionMiddleware, secret_key="pig-checkin-super-secret-key-lazy-is-best")

try:
    import importlib.metadata
    APP_VERSION = importlib.metadata.version("pig-checkin")
except Exception:
    APP_VERSION = "0.2.4"

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

@app.on_event("startup")
def startup():
    init_db()
    start_scheduler()

def get_current_user(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})

@app.post("/login", response_class=HTMLResponse)
def login_action(request: Request, username: str = Form(...), password: str = Form(...)):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and verify_password(user["password"], password):
            request.session["user"] = username
            return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": "用户名或密码错误"})

def is_api_request(request: Request) -> bool:
    accept = request.headers.get("Accept", "")
    return "application/json" in accept or request.headers.get("X-Requested-With") == "XMLHttpRequest"

def get_dashboard_state():
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

            # 仅在前端下发是否已填写的状态标识，凭据脱敏置空，不在前端暴露真实明文或密文
            s["has_cookies"] = bool(s.get("cookies") and str(s["cookies"]).strip())
            s["has_username"] = bool(s.get("username") and str(s["username"]).strip())
            s["has_password"] = bool(s.get("password") and str(s["password"]).strip())
            s["cookies"] = ""
            s["username"] = ""
            s["password"] = ""

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
        if is_api_request(request):
            return JSONResponse({"ok": False, "message": "用户名不能为空"}, status_code=400)
        return RedirectResponse(url="/?pwd_err=用户名不能为空", status_code=303)

    if new_password and new_password != confirm_password:
        if is_api_request(request):
            return JSONResponse({"ok": False, "message": "两次输入的新密码不一致"}, status_code=400)
        return RedirectResponse(url="/?pwd_err=两次输入的新密码不一致", status_code=303)

    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (user,)).fetchone()
        if not row or not verify_password(row["password"], old_password):
            if is_api_request(request):
                return JSONResponse({"ok": False, "message": "原密码不正确"}, status_code=400)
            return RedirectResponse(url="/?pwd_err=原密码不正确", status_code=303)

        if new_username != user:
            conflict = conn.execute("SELECT id FROM users WHERE username = ?", (new_username,)).fetchone()
            if conflict:
                if is_api_request(request):
                    return JSONResponse({"ok": False, "message": "新用户名已被占用"}, status_code=400)
                return RedirectResponse(url="/?pwd_err=新用户名已被占用", status_code=303)

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

    request.session["user"] = new_username
    if is_api_request(request):
        return JSONResponse({"ok": True, "message": "账号信息修改成功", "new_username": new_username})
    return RedirectResponse(url="/?pwd_ok=账号信息修改成功", status_code=303)

@app.get("/api/dashboard-data")
def api_dashboard_data(user: str = Depends(get_current_user)):
    sites, logs = get_dashboard_state()
    return {
        "ok": True,
        "current_user": user,
        "app_version": APP_VERSION,
        "sites": sites,
        "logs": logs
    }

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = request.session.get("user")
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
        "pwd_err": request.query_params.get("pwd_err"),
        "pwd_ok": request.query_params.get("pwd_ok")
    })

@app.post("/add-site")
def add_site(
    request: Request,
    name: str = Form(...),
    site_type: str = Form("cnlang"),
    domain: str = Form("https://cnlang.org"),
    cookies: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    schedule_type: str = Form("random"),
    schedule_time: str = Form("09:00"),
    max_retries: int = Form(3),
    retry_interval: int = Form(60),
    user: str = Depends(get_current_user)
):
    auth_type = tpl_lib.get_template_auth_type(site_type)
    initial_next_run = compute_initial_next_run(schedule_type, schedule_time)
    enc_cookies = encrypt_text(cookies.strip())
    enc_username = encrypt_text(username.strip())
    enc_password = encrypt_text(password.strip())
    with get_db() as conn:
        conn.execute("""
            INSERT INTO sites (name, site_type, domain, auth_type, cookies, username, password, schedule_type, schedule_time, max_retries, retry_interval, enabled, next_run, last_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, '等待签到')
        """, (name, site_type, domain, auth_type, enc_cookies, enc_username, enc_password, schedule_type, schedule_time, max_retries, retry_interval, initial_next_run))
        conn.commit()
    if is_api_request(request):
        return JSONResponse({"ok": True, "message": f"成功添加站点 [{name}]"})
    return RedirectResponse(url="/", status_code=303)

@app.post("/edit-site")
def edit_site(
    request: Request,
    id: int = Form(...),
    name: str = Form(...),
    site_type: str = Form("cnlang"),
    domain: str = Form("https://cnlang.org"),
    cookies: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    schedule_type: str = Form("random"),
    schedule_time: str = Form("09:00"),
    max_retries: int = Form(3),
    retry_interval: int = Form(60),
    enabled: int = Form(1),
    user: str = Depends(get_current_user)
):
    auth_type = tpl_lib.get_template_auth_type(site_type)
    initial_next_run = compute_initial_next_run(schedule_type, schedule_time)
    
    with get_db() as conn:
        old_site = conn.execute("SELECT cookies, username, password FROM sites WHERE id = ?", (id,)).fetchone()
        
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

        conn.execute("""
            UPDATE sites 
            SET name = ?, site_type = ?, domain = ?, auth_type = ?, cookies = ?, username = ?, password = ?,
                schedule_type = ?, schedule_time = ?, max_retries = ?, retry_interval = ?, enabled = ?, next_run = ?
            WHERE id = ?
        """, (name, site_type, domain, auth_type, enc_cookies, enc_username, enc_password, schedule_type, schedule_time, max_retries, retry_interval, enabled, initial_next_run, id))
        conn.commit()
    if is_api_request(request):
        return JSONResponse({"ok": True, "message": f"成功更新站点 [{name}] 配置"})
    return RedirectResponse(url="/", status_code=303)

@app.get("/toggle-site/{site_id}")
def toggle_site(request: Request, site_id: int, user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("UPDATE sites SET enabled = 1 - enabled WHERE id = ?", (site_id,))
        conn.commit()
    if is_api_request(request):
        return JSONResponse({"ok": True, "message": "定时开关状态已更新"})
    return RedirectResponse(url="/", status_code=303)

@app.get("/delete-site/{site_id}")
def delete_site(request: Request, site_id: int, user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        conn.commit()
    if is_api_request(request):
        return JSONResponse({"ok": True, "message": "站点已成功删除"})
    return RedirectResponse(url="/", status_code=303)

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
def run_site(request: Request, site_id: int, user: str = Depends(get_current_user)):
    with get_db() as conn:
        site = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site:
            if is_api_request(request):
                return JSONResponse({"ok": False, "message": "站点不存在"}, status_code=404)
            return RedirectResponse(url="/", status_code=303)
        site_dict = dict(site)

    ok, message = execute_checkin(site_dict)
    with get_db() as conn:
        updated = conn.execute("SELECT last_status, last_run FROM sites WHERE id = ?", (site_id,)).fetchone()
        status_text = updated["last_status"] if updated else ("签到成功" if ok else "签到失败")
        now_str = updated["last_run"] if updated else ""

    if is_api_request(request):
        return JSONResponse({"ok": ok, "message": message, "status": status_text, "last_run": now_str})
    return RedirectResponse(url="/", status_code=303)

@app.get("/run-all")
def run_all(request: Request, user: str = Depends(get_current_user)):
    with get_db() as conn:
        sites = conn.execute("SELECT * FROM sites WHERE enabled = 1").fetchall()

    success_count = 0
    fail_count = 0
    for site in sites:
        site_dict = dict(site)
        ok, message = execute_checkin(site_dict)
        if ok:
            success_count += 1
        else:
            fail_count += 1

    if is_api_request(request):
        return JSONResponse({
            "ok": True,
            "message": f"全部签到完成：成功 {success_count} 个，失败 {fail_count} 个",
            "success_count": success_count,
            "fail_count": fail_count
        })
    return RedirectResponse(url="/", status_code=303)

@app.get("/clear-logs")
def clear_logs(request: Request, user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM logs")
        conn.commit()
    if is_api_request(request):
        return JSONResponse({"ok": True, "message": "签到日志已清空"})
    return RedirectResponse(url="/", status_code=303)

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
