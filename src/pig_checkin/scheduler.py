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

def submit_site(site_id: int) -> bool:
    """把站点签到提交到线程池执行，立即返回。

    同一站点已在执行时返回 False —— 手动「立即签到」与定时任务共用这一道锁，
    避免同一个站点被并发跑两遍（浏览器实例也会翻倍）。
    """
    with _lock:
        if site_id in _running_site_ids:
            return False
        _running_site_ids.add(site_id)
    executor.submit(run_site_checkin, site_id)
    return True

def running_site_ids() -> set:
    with _lock:
        return set(_running_site_ids)

def try_auto_sync_cookiecloud():
    """若启用了 CookieCloud 自动同步，拉取最新凭据并更新本地站点。"""
    try:
        from pig_checkin.cookiecloud import (
            get_cookiecloud_config,
            fetch_and_decrypt_cookiecloud,
            _extract_cookie_map,
            sync_cookies_to_sites
        )
        cfg = get_cookiecloud_config()
        if cfg["auto_sync"] and cfg["url"] and cfg["uuid"] and cfg["password"]:
            ok, _, data = fetch_and_decrypt_cookiecloud(cfg["url"], cfg["uuid"], cfg["password"])
            if ok:
                cmap = _extract_cookie_map(data)
                synced = sync_cookies_to_sites(cmap)
                if synced:
                    logger.info("☁️ [CookieCloud] 自动同步已更新 %d 个站点的 Cookies: %s",
                                len(synced), [s["name"] for s in synced])
    except Exception as e:
        logger.warning("☁️ [CookieCloud] 自动同步异常: %s", e)

def _scheduler_loop():
    last_cc_sync = 0.0
    while True:
        try:
            now_ts = time.time()
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with get_db() as conn:
                # 一次连接顺带读取配置，避免循环内多次打开关闭数据库连接
                cc_row = conn.execute("SELECT value FROM settings WHERE key = 'cookiecloud_auto_sync'").fetchone()
                if cc_row and cc_row["value"] == "1":
                    int_row = conn.execute("SELECT value FROM settings WHERE key = 'cookiecloud_sync_interval'").fetchone()
                    try:
                        interval_sec = max(300, int(int_row["value"]) * 60) if int_row else 3600
                    except Exception:
                        interval_sec = 3600

                    if now_ts - last_cc_sync >= interval_sec:
                        last_cc_sync = now_ts
                        # 提交给线程池后台异步执行，绝不因为 CookieCloud 网络耗时阻塞主调度循环
                        executor.submit(try_auto_sync_cookiecloud)

                sites = conn.execute(
                    "SELECT id FROM sites WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?", 
                    (now_str,)
                ).fetchall()
            
            for site in sites:
                sid = site["id"]
                if submit_site(sid):
                    logger.info("⏰ [定时调度] 触发站点任务: ID=%d 到达计划时刻 (%s)，提交执行", sid, now_str)
        except Exception:
            pass
        time.sleep(10)

def start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
