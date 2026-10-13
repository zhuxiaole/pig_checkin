import os
import base64
import hashlib
from cryptography.fernet import Fernet

DEFAULT_SECRET_KEY = "pig-checkin-default-secret-key-please-change-in-production"

def get_fernet() -> Fernet:
    raw_key = os.environ.get("SECRET_KEY", "").strip() or DEFAULT_SECRET_KEY
    key_32 = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())
    return Fernet(key_32)

def is_encrypted(text: str) -> bool:
    if not text or not text.startswith("gAAAAA"):
        return False
    try:
        get_fernet().decrypt(text.encode("utf-8"))
        return True
    except Exception:
        return False

def encrypt_text(plain: str) -> str:
    if not plain:
        return ""
    if is_encrypted(plain):
        return plain
    return get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")

def decrypt_text(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        return get_fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
    except Exception:
        # 兼容未加密历史数据或未知密文
        return cipher
