import os
import datetime
from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from pig_checkin.db import init_db, get_db, verify_password, hash_password
from pig_checkin.checkin import execute_checkin
from pig_checkin.scheduler import start_scheduler, compute_initial_next_run, compute_next_day_run
from pig_checkin.templates_config import SITE_TEMPLATES, get_template_auth_type

app = FastAPI(title="Pig Checkin Service")
app.add_middleware(SessionMiddleware, secret_key="pig-checkin-super-secret-key-lazy-is-best")

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
        return RedirectResponse(url="/?pwd_err=用户名不能为空", status_code=303)

    if new_password and new_password != confirm_password:
        return RedirectResponse(url="/?pwd_err=两次输入的新密码不一致", status_code=303)

    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (user,)).fetchone()
        if not row or not verify_password(row["password"], old_password):
            return RedirectResponse(url="/?pwd_err=原密码不正确", status_code=303)

        if new_username != user:
            conflict = conn.execute("SELECT id FROM users WHERE username = ?", (new_username,)).fetchone()
            if conflict:
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
    return RedirectResponse(url="/?pwd_ok=账号信息修改成功", status_code=303)

@app.post("/change-password")
def change_password(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
    user: str = Depends(get_current_user)
):
    return change_account(request, new_username=user, old_password=old_password, new_password=new_password, user=user)

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    with get_db() as conn:
        sites_cur = conn.execute("SELECT * FROM sites ORDER BY id ASC").fetchall()
        sites = [dict(s) for s in sites_cur]
        for s in sites:
            if not s.get("enabled"):
                s["next_run_display"] = "已停用"
            elif s.get("next_run"):
                s["next_run_display"] = str(s["next_run"])
            else:
                s["next_run_display"] = "待计算"
        logs_cur = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 50").fetchall()
        logs = [dict(l) for l in logs_cur]
    
    return templates.TemplateResponse(request, "dashboard.html", {
        "current_user": user,
        "sites": sites,
        "logs": logs,
        "site_templates": SITE_TEMPLATES,
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
    auth_type = get_template_auth_type(site_type)
    initial_next_run = compute_initial_next_run(schedule_type, schedule_time)
    with get_db() as conn:
        conn.execute("""
            INSERT INTO sites (name, site_type, domain, auth_type, cookies, username, password, schedule_type, schedule_time, max_retries, retry_interval, enabled, next_run, last_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, '等待签到')
        """, (name, site_type, domain, auth_type, cookies, username, password, schedule_type, schedule_time, max_retries, retry_interval, initial_next_run))
        conn.commit()
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
    auth_type = get_template_auth_type(site_type)
    initial_next_run = compute_initial_next_run(schedule_type, schedule_time)
    with get_db() as conn:
        conn.execute("""
            UPDATE sites 
            SET name = ?, site_type = ?, domain = ?, auth_type = ?, cookies = ?, username = ?, password = ?,
                schedule_type = ?, schedule_time = ?, max_retries = ?, retry_interval = ?, enabled = ?, next_run = ?
            WHERE id = ?
        """, (name, site_type, domain, auth_type, cookies, username, password, schedule_type, schedule_time, max_retries, retry_interval, enabled, initial_next_run, id))
        conn.commit()
    return RedirectResponse(url="/", status_code=303)

@app.get("/toggle-site/{site_id}")
def toggle_site(site_id: int, user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("UPDATE sites SET enabled = 1 - enabled WHERE id = ?", (site_id,))
        conn.commit()
    return RedirectResponse(url="/", status_code=303)

@app.get("/delete-site/{site_id}")
def delete_site(site_id: int, user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        conn.commit()
    return RedirectResponse(url="/", status_code=303)

@app.get("/run-site/{site_id}")
def run_site(site_id: int, user: str = Depends(get_current_user)):
    with get_db() as conn:
        site = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site:
            return RedirectResponse(url="/", status_code=303)
        site_dict = dict(site)

    ok, message = execute_checkin(site_dict)
    status_text = "签到成功" if ok else "签到失败"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        if ok:
            next_run_str = compute_next_day_run(site_dict.get("schedule_type", "random"), site_dict.get("schedule_time", "09:00"))
            conn.execute(
                "UPDATE sites SET last_status = ?, last_run = ?, next_run = ? WHERE id = ?",
                (status_text, now_str, next_run_str, site_id)
            )
        else:
            conn.execute(
                "UPDATE sites SET last_status = ?, last_run = ? WHERE id = ?",
                (status_text, now_str, site_id)
            )
        conn.execute(
            "INSERT INTO logs (site_id, site_name, status, message) VALUES (?, ?, ?, ?)",
            (site_id, site_dict["name"], "成功" if ok else "失败", message)
        )
        conn.commit()

    return RedirectResponse(url="/", status_code=303)

@app.get("/run-all")
def run_all(user: str = Depends(get_current_user)):
    with get_db() as conn:
        sites = conn.execute("SELECT * FROM sites WHERE enabled = 1").fetchall()

    for site in sites:
        site_dict = dict(site)
        ok, message = execute_checkin(site_dict)
        status_text = "签到成功" if ok else "签到失败"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            if ok:
                next_run_str = compute_next_day_run(site_dict.get("schedule_type", "random"), site_dict.get("schedule_time", "09:00"))
                conn.execute(
                    "UPDATE sites SET last_status = ?, last_run = ?, next_run = ? WHERE id = ?",
                    (status_text, now_str, next_run_str, site_dict["id"])
                )
            else:
                conn.execute(
                    "UPDATE sites SET last_status = ?, last_run = ? WHERE id = ?",
                    (status_text, now_str, site_dict["id"])
                )
            conn.execute(
                "INSERT INTO logs (site_id, site_name, status, message) VALUES (?, ?, ?, ?)",
                (site_dict["id"], site_dict["name"], "成功" if ok else "失败", message)
            )
            conn.commit()

    return RedirectResponse(url="/", status_code=303)

@app.get("/clear-logs")
def clear_logs(user: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM logs")
        conn.commit()
    return RedirectResponse(url="/", status_code=303)
