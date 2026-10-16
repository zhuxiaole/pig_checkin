import base64
import hashlib
import json
import logging
import urllib.parse
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import requests

from pig_checkin.db import get_db, get_setting, set_setting
from pig_checkin.crypto import encrypt_text, decrypt_text

logger = logging.getLogger("pig_checkin")


def evp_bytes_to_key(password: bytes, salt: bytes, key_len=32, iv_len=16) -> tuple[bytes, bytes]:
    """OpenSSL EVP_BytesToKey using MD5 and 1 iteration (CryptoJS default)."""
    d = b""
    d_i = b""
    while len(d) < (key_len + iv_len):
        d_i = hashlib.md5(d_i + password + salt).digest()
        d += d_i
    return d[:key_len], d[key_len:key_len + iv_len]


def decrypt_cookiecloud_data(encrypted_b64: str, uuid: str, password: str) -> dict:
    raw = base64.b64decode(encrypted_b64)
    if not raw.startswith(b"Salted__"):
        raise ValueError("密文缺少 Salted__ 头部特征，无法解密")
    salt = raw[8:16]
    ciphertext = raw[16:]

    key_password = hashlib.md5(f"{uuid}-{password}".encode("utf-8")).hexdigest()[:16].encode("utf-8")
    key, iv = evp_bytes_to_key(key_password, salt, 32, 16)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded_plain = decryptor.update(ciphertext) + decryptor.finalize()

    pad_len = padded_plain[-1]
    if pad_len > 16 or pad_len == 0 or padded_plain[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("解密填充校验失败，端对端加密密码可能不正确")
    
    plaintext = padded_plain[:-pad_len].decode("utf-8", errors="replace")
    return json.loads(plaintext)


def get_cookiecloud_config() -> dict:
    url = get_setting("cookiecloud_url", "")
    uuid = get_setting("cookiecloud_uuid", "")
    enc_pw = get_setting("cookiecloud_password", "")
    auto_sync = get_setting("cookiecloud_auto_sync", "0") == "1"
    
    pw = decrypt_text(enc_pw) if enc_pw else ""
    return {
        "url": url,
        "uuid": uuid,
        "password": pw,
        "has_password": bool(enc_pw),
        "auto_sync": auto_sync,
    }


def save_cookiecloud_config(url: str, uuid: str, password: str, auto_sync: bool):
    url = url.strip().rstrip("/")
    uuid = uuid.strip()
    password = password.strip()
    
    set_setting("cookiecloud_url", url)
    set_setting("cookiecloud_uuid", uuid)
    set_setting("cookiecloud_auto_sync", "1" if auto_sync else "0")
    
    if password:
        set_setting("cookiecloud_password", encrypt_text(password))


def fetch_and_decrypt_cookiecloud(url: str, uuid: str, password: str) -> tuple[bool, str, dict]:
    if not url or not uuid or not password:
        return False, "CookieCloud 服务器地址、用户 KEY 或加密密码未配置完整", {}

    target_url = f"{url.rstrip('/')}/get/{uuid}"
    try:
        res = requests.get(target_url, headers={"User-Agent": "PigCheckin/CookieCloud"}, timeout=15)
    except Exception as e:
        return False, f"连接 CookieCloud 服务器失败: {e}", {}

    if res.status_code != 200:
        return False, f"CookieCloud 服务器返回 HTTP {res.status_code}", {}

    try:
        data = res.json()
    except Exception:
        return False, "CookieCloud 服务器返回内容非 JSON 格式", {}

    encrypted = data.get("encrypted")
    if not encrypted:
        return False, "服务器未返回任何加密数据 (encrypted 为空)", {}

    try:
        decrypted = decrypt_cookiecloud_data(encrypted, uuid, password)
        return True, "获取并解密成功", decrypted
    except Exception as e:
        return False, f"CookieCloud 数据解密失败: {e}", {}


def _extract_cookie_map(decrypted: dict) -> dict[str, list[dict]]:
    """支持新版 {cookie_data: {domain: [...]}} 与旧版 {domain: [...]}"""
    if isinstance(decrypted.get("cookie_data"), dict):
        return decrypted["cookie_data"]
    return {k: v for k, v in decrypted.items() if isinstance(v, list)}


def _match_domain_cookies(site_host: str, cookie_map: dict[str, list[dict]]) -> dict[str, str]:
    """匹配站点域名下的所有 Cookie 并合并成 {name: value} 字典。"""
    site_host = site_host.lower().strip()
    merged: dict[str, str] = {}
    
    for domain_key, cookies in cookie_map.items():
        clean_key = domain_key.lstrip(".").lower().strip()
        # 匹配: 完全一致、或者主域名匹配 (如 audiences.me 匹配 .audiences.me, www.audiences.me 匹配 audiences.me)
        if clean_key == site_host or site_host.endswith("." + clean_key) or clean_key.endswith("." + site_host):
            for c in cookies:
                if isinstance(c, dict) and "name" in c and "value" in c:
                    merged[c["name"]] = c["value"]
    return merged


def sync_cookies_to_sites(cookie_map: dict[str, list[dict]]) -> list[dict]:
    """将从 CookieCloud 解析出的 Cookie 匹配并加密写入 sites 表中的 cookie 认证站点。"""
    updated_sites = []
    with get_db() as conn:
        rows = conn.execute("SELECT id, name, domain, auth_type FROM sites WHERE auth_type = 'cookie'").fetchall()
        for r in rows:
            site_id = r["id"]
            site_name = r["name"]
            domain_str = r["domain"] or ""
            
            parsed = urllib.parse.urlparse(domain_str)
            host = parsed.hostname or parsed.netloc or domain_str
            if not host:
                continue
            
            matched_dict = _match_domain_cookies(host, cookie_map)
            if matched_dict:
                cookie_str = "; ".join(f"{k}={v}" for k, v in matched_dict.items())
                enc_cookies = encrypt_text(cookie_str)
                conn.execute("UPDATE sites SET cookies = ? WHERE id = ?", (enc_cookies, site_id))
                updated_sites.append({
                    "id": site_id,
                    "name": site_name,
                    "host": host,
                    "count": len(matched_dict)
                })
        conn.commit()
    return updated_sites
