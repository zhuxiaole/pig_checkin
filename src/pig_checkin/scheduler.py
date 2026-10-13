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

def compute_next_run(schedule_type: str, schedule_time: str, next_day: bool = False) -> str:
    now = datetime.datetime.now()
    if schedule_type == "fixed":
        try:
            h, m = map(int, (schedule_time or "09:00").split(":"))
        except Exception:
            h, m = 9, 0
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if next_day or target <= now:
            target += datetime.timedelta(days=1)
        return target.strftime("%Y-%m-%d %H:%M:%S")

    cur_sec = now.hour * 3600 + now.minute * 60 + now.second
    day_end = 23 * 3600 + 59 * 60
    if not next_day and day_end - cur_sec > 120:
        sec = random.randint(cur_sec + 60, day_end)
        target = now.replace(hour=sec // 3600, minute=(sec % 3600) // 60, second=sec % 60, microsecond=0)
    else:
        target = (now + datetime.timedelta(days=1)).replace(
            hour=random.randint(8, 21), minute=random.randint(0, 59), second=random.randint(0, 59), microsecond=0
        )
    return target.strftime("%Y-%m-%d %H:%M:%S")

def run_site_checkin(site_id: int):
    try:
        with get_db() as conn:
            site = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if not site:
                return
            site_dict = dict(site)

        execute_checkin(site_dict)
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
