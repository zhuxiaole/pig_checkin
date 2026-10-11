import sqlite3
import hashlib
import hmac
import secrets
import shutil
from pathlib import Path

OLD_DB_PATH = Path(__file__).resolve().parent / "checkin.db"
DB_DIR = Path.cwd() / "data"
DB_PATH = DB_DIR / "checkin.db"

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists() and OLD_DB_PATH.exists():
        shutil.copy2(OLD_DB_PATH, DB_PATH)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    pw_hash = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{pw_hash}"

def verify_password(stored: str, password: str) -> bool:
    if ":" not in stored:
        return False
    salt, expected = stored.split(":", 1)
    computed = hashlib.sha256((salt + password).encode()).hexdigest()
    return hmac.compare_digest(expected, computed)

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            site_type TEXT NOT NULL DEFAULT 'cnlang',
            auth_type TEXT NOT NULL DEFAULT 'cookie',
            cookies TEXT DEFAULT '',
            username TEXT DEFAULT '',
            password TEXT DEFAULT '',
            domain TEXT DEFAULT 'https://cnlang.org',
            schedule_type TEXT DEFAULT 'random', -- 'random' or 'fixed'
            schedule_time TEXT DEFAULT '09:00',
            max_retries INTEGER DEFAULT 3,
            retry_interval INTEGER DEFAULT 60,
            enabled INTEGER DEFAULT 1,
            next_run TIMESTAMP,
            last_status TEXT DEFAULT '未执行',
            last_run TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER,
            site_name TEXT,
            status TEXT NOT NULL,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE SET NULL
        );
        """)

        # 增量字段迁移 (兼容旧表结构)
        existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(sites)").fetchall()]
        column_defs = {
            "domain": "TEXT DEFAULT 'https://cnlang.org'",
            "schedule_type": "TEXT DEFAULT 'random'",
            "schedule_time": "TEXT DEFAULT '09:00'",
            "max_retries": "INTEGER DEFAULT 3",
            "retry_interval": "INTEGER DEFAULT 60",
            "next_run": "TIMESTAMP"
        }
        for col, col_type in column_defs.items():
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE sites ADD COLUMN {col} {col_type}")

        # 默认管理员
        row = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                ("admin", hash_password("admin123"))
            )

        # 初始默认站点 cnlang.org (仅全新安装、表内没有任何站点时创建)
        site_count = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
        if site_count == 0:
            from pig_checkin.scheduler import compute_initial_next_run
            init_next = compute_initial_next_run("random", "09:00")
            conn.execute("""
                INSERT INTO sites (name, site_type, auth_type, domain, schedule_type, schedule_time, max_retries, retry_interval, enabled, next_run, last_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, '等待签到')
            """, ("cnlang.org", "cnlang", "cookie", "https://cnlang.org", "random", "09:00", 3, 60, init_next))
        conn.commit()
