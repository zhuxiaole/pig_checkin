import time
import random
import threading
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor
from pig_checkin.db import get_db
from pig_checkin.checkin import execute_checkin

logger = logging.getLogger("pig_checkin")

# ponytail: 固定 3 个 worker 控制单机并发与浏览器实例数；超千站再上分布式队列
executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="checkin_worker")
_running_site_ids = set()
_lock = threading.Lock()

def compute_initial_next_run(schedule_type: str, schedule_time: str) -> str:
    """创建网站时计算下一次执行时间"""
    now = datetime.datetime.now()
    if schedule_type == "fixed":
        try:
            h, m = map(int, (schedule_time or "09:00").split(":"))
        except Exception:
            h, m = 9, 0
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        return target.strftime("%Y-%m-%d %H:%M:%S")

    cur_total_sec = now.hour * 3600 + now.minute * 60 + now.second
    day_end_sec = 23 * 3600 + 59 * 60
    if day_end_sec - cur_total_sec > 120:
        rand_sec = random.randint(cur_total_sec + 60, day_end_sec)
        target = now.replace(hour=rand_sec // 3600, minute=(rand_sec % 3600) // 60, second=rand_sec % 60, microsecond=0)
    else:
        tomorrow = now + datetime.timedelta(days=1)
        rand_h = random.randint(8, 21)
        rand_m = random.randint(0, 59)
        rand_s = random.randint(0, 59)
        target = tomorrow.replace(hour=rand_h, minute=rand_m, second=rand_s, microsecond=0)
    return target.strftime("%Y-%m-%d %H:%M:%S")

def compute_next_day_run(schedule_type: str, schedule_time: str) -> str:
    """任务执行成功后计算下一次（明天的）执行时间"""
    now = datetime.datetime.now()
    tomorrow = now + datetime.timedelta(days=1)
    if schedule_type == "fixed":
        try:
            h, m = map(int, (schedule_time or "09:00").split(":"))
        except Exception:
            h, m = 9, 0
        target = tomorrow.replace(hour=h, minute=m, second=0, microsecond=0)
    else:
        rand_h = random.randint(8, 21)
        rand_m = random.randint(0, 59)
        rand_s = random.randint(0, 59)
        target = tomorrow.replace(hour=rand_h, minute=rand_m, second=rand_s, microsecond=0)
    return target.strftime("%Y-%m-%d %H:%M:%S")

def run_site_checkin(site_id: int):
    try:
        with get_db() as conn:
            site = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if not site:
                return
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
    finally:
        with _lock:
            _running_site_ids.discard(site_id)

def _scheduler_loop():
    while True:
        try:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with get_db() as conn:
                sites = conn.execute(
                    "SELECT id FROM sites WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?", 
                    (now_str,)
                ).fetchall()
            
            for site in sites:
                sid = site["id"]
                with _lock:
                    if sid not in _running_site_ids:
                        _running_site_ids.add(sid)
                        logger.info("⏰ [定时调度] 触发站点任务: ID=%d 到达计划时刻 (%s)，提交执行", sid, now_str)
                        executor.submit(run_site_checkin, sid)
        except Exception:
            pass
        time.sleep(10)

def start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
