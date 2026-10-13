import sqlite3
import hashlib
import hmac
import re
import secrets
import shutil
from pathlib import Path

OLD_DB_PATH = Path(__file__).resolve().parent / "checkin.db"
DB_DIR = Path.cwd() / "data"
DB_PATH = DB_DIR / "checkin.db"

# 密码存储：scrypt 慢哈希（旧数据是单次 sha256，验证时兼容，登录成功即升级）
# 存储格式：scrypt$n$r$p$盐$哈希；旧格式：盐:哈希
SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN = 2 ** 14, 8, 1, 32
SCRYPT_MAXMEM = 64 * 1024 * 1024   # OpenSSL 默认上限只有 32MB，显式给足避免以后调高 n 时炸掉

# 密码格式与强度要求（服务端是唯一权威，前端只用 HTML 原生属性做粗筛）
PASSWORD_MIN, PASSWORD_MAX = 8, 64
COMMON_PASSWORDS = {
    "12345678", "123456789", "1234567890", "password", "password1", "passw0rd",
    "admin123", "administrator", "qwerty123", "abc12345", "11111111", "88888888",
    "iloveyou", "letmein1", "welcome1", "sunshine1", "monkey123", "football1",
}

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                            p=SCRYPT_P, dklen=SCRYPT_DKLEN, maxmem=SCRYPT_MAXMEM)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"

def verify_password(stored: str, password: str) -> bool:
    if not stored or not password:
        return False

    if stored.startswith("scrypt$"):
        try:
            _, n, r, p, salt_hex, expected = stored.split("$")
            digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                    n=int(n), r=int(r), p=int(p), dklen=len(expected) // 2,
                                    maxmem=SCRYPT_MAXMEM)
        except Exception:
            return False   # 存储串损坏时一律判失败，绝不放行
        return hmac.compare_digest(expected, digest.hex())

    # 旧 sha256(盐 + 密码) 格式
    if ":" not in stored:
        return False
    salt, expected = stored.split(":", 1)
    computed = hashlib.sha256((salt + password).encode()).hexdigest()
    return hmac.compare_digest(expected, computed)

def needs_rehash(stored: str) -> bool:
    """旧格式密码在下次登录成功后顺手升级到 scrypt。"""
    return bool(stored) and not stored.startswith("scrypt$")

def password_error(password: str, username: str = "") -> str | None:
    """校验密码格式与强度，返回错误说明；None 表示可用。"""
    if not password or not password.strip():
        return "密码不能为空"
    if len(password) < PASSWORD_MIN:
        return f"密码至少 {PASSWORD_MIN} 位"
    if len(password) > PASSWORD_MAX:
        return f"密码最多 {PASSWORD_MAX} 位"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "密码需同时包含字母和数字"
    if password.lower() in COMMON_PASSWORDS:
        return "该密码过于常见，请更换"
    if username and username.lower() in password.lower():
        return "密码不能包含用户名"
    return None

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
