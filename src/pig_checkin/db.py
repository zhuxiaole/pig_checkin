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
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists() and OLD_DB_PATH.exists():
        shutil.copy2(OLD_DB_PATH, DB_PATH)
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
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE SET NULL
        );

        -- 已启用的模板 key：模板库来自代码，这里只记录用户启用了哪些。
        -- 默认不启用任何模板，全新部署需自行在「模板库」里勾选。
        CREATE TABLE IF NOT EXISTS enabled_templates (
            key TEXT PRIMARY KEY
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

        # 自动加密现有历史明文凭据 (cookies, username, password)
        from pig_checkin.crypto import encrypt_text, is_encrypted
        site_rows = conn.execute("SELECT id, cookies, username, password FROM sites").fetchall()
        for r in site_rows:
            sid = r["id"]
            ck = r["cookies"] or ""
            un = r["username"] or ""
            pw = r["password"] or ""
            needs_update = False
            new_ck, new_un, new_pw = ck, un, pw
            if ck and not is_encrypted(ck):
                new_ck = encrypt_text(ck)
                needs_update = True
            if un and not is_encrypted(un):
                new_un = encrypt_text(un)
                needs_update = True
            if pw and not is_encrypted(pw):
                new_pw = encrypt_text(pw)
                needs_update = True
            if needs_update:
                conn.execute(
                    "UPDATE sites SET cookies = ?, username = ?, password = ? WHERE id = ?",
                    (new_ck, new_un, new_pw, sid)
                )
        conn.commit()
