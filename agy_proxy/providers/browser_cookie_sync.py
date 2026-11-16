"""
Browser Cookie and Session Synchronizer for Web Providers.
Extracts live session tokens, cookies, and authentication headers directly from
locally installed browser profiles (Helium, Chrome, Chromium, LibreWolf, Brave, etc.)
without requiring Chrome to be launched with --remote-debugging-port=9222.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
from typing import Any

import httpx

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

logger = logging.getLogger("agy_proxy.providers.browser_cookie_sync")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Safari/537.36"
)

# Standard Chromium Linux OSCrypt PBKDF2 parameters
LINUX_SALT = b"saltysalt"
LINUX_IV = b" " * 16
LINUX_KEY_LEN = 16
LINUX_ITERATIONS = 1
LINUX_PEANUTS_KEY = None

if HAS_CRYPTO:
    try:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA1(),
            length=LINUX_KEY_LEN,
            salt=LINUX_SALT,
            iterations=LINUX_ITERATIONS,
        )
        LINUX_PEANUTS_KEY = kdf.derive(b"peanuts")
    except Exception as _kdf_err:
        logger.debug("Failed to derive Linux peanuts key: %s", _kdf_err)


def decrypt_chromium_cookie_value(enc: bytes) -> str:
    """
    Decrypts an encrypted cookie value from a Chromium/Chrome SQLite database on Linux.
    Chromium prefixes encrypted values with 'v10' or 'v11'.
    Plaintext structure has a 32-byte header/signature followed by payload and PKCS7 padding.
    """
    if not enc:
        return ""
    if not (enc.startswith(b"v10") or enc.startswith(b"v11")):
        return enc.decode("utf-8", errors="ignore")

    if not HAS_CRYPTO or not LINUX_PEANUTS_KEY:
        return ""

    try:
        ciphertext = enc[3:]
        cipher = Cipher(algorithms.AES(LINUX_PEANUTS_KEY), modes.CBC(LINUX_IV))
        dec = cipher.decryptor().update(ciphertext) + cipher.decryptor().finalize()
        pad = dec[-1]
        val = dec[32:-pad] if (0 < pad <= 16 and len(dec) > 32 + pad) else dec[32:]
        return val.decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug("Failed to decrypt cookie with Linux key: %s", e)
        return ""


def find_browser_cookie_databases() -> list[tuple[str, str]]:
    """
    Finds all active browser cookie SQLite databases on the system.
    Returns a list of (browser_name, file_path) tuples, sorted by last modified timestamp
    so the most recently active browser profile is inspected first.
    """
    home = os.path.expanduser("~")
    search_patterns = [
        ("Helium", os.path.join(home, ".config/net.imput.helium/*/Cookies")),
        ("Helium", os.path.join(home, ".config/helium-*/*/Cookies")),
        ("Chrome", os.path.join(home, ".config/google-chrome/*/Cookies")),
        ("Chromium", os.path.join(home, ".config/chromium/*/Cookies")),
        ("Chrome Testing", os.path.join(home, ".config/google-chrome-for-testing/*/Cookies")),
        ("Brave", os.path.join(home, ".config/BraveSoftware/Brave-Browser/*/Cookies")),
        ("Antigravity", os.path.join(home, ".config/Antigravity/Cookies")),
    ]

    found: list[tuple[str, str, float]] = []
    for b_name, pattern in search_patterns:
        for fpath in glob.glob(pattern):
            if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                try:
                    mtime = os.path.getmtime(fpath)
                    found.append((b_name, fpath, mtime))
                except OSError:
                    pass

    # Sort descending by mtime (most recent first)
    found.sort(key=lambda x: x[2], reverse=True)
    return [(b_name, fpath) for b_name, fpath, _ in found]


def read_cookies_from_sqlite(db_path: str, domain_filters: list[str]) -> dict[str, str]:
    """
    Safely reads and decrypts cookies matching domain_filters from a Chromium SQLite database.
    Copies database to a temporary file first to avoid SQLite database locks.
    """
    if not os.path.isfile(db_path) or not HAS_CRYPTO:
        return {}

    cookies: dict[str, str] = {}
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name
        shutil.copyfile(db_path, tmp_path)

        conn = sqlite3.connect(tmp_path, timeout=3.0)
        cursor = conn.cursor()

        # Build SQL condition
        clauses = " OR ".join(["host_key LIKE ?" for _ in domain_filters])
        query = f"SELECT name, encrypted_value FROM cookies WHERE {clauses}"
        params = [f"%{d}%" for d in domain_filters]

        cursor.execute(query, params)
        rows = cursor.fetchall()

        for name, enc in rows:
            val = decrypt_chromium_cookie_value(enc)
            if val:
                cookies[name] = val

        conn.close()
    except Exception as e:
        logger.debug("Failed reading cookies from %s: %s", db_path, e)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return cookies


async def extract_chatgpt_from_browser() -> dict[str, Any]:
    """
    Inspects installed browser profiles for ChatGPT Web session cookies,
    calls https://chatgpt.com/api/auth/session, and returns fresh Bearer accessToken.
    """
    db_list = find_browser_cookie_databases()
    if not db_list:
        raise RuntimeError("No Chromium/Chrome browser profiles found on system.")

    last_err = None
    for b_name, db_path in db_list:
        cookies = read_cookies_from_sqlite(db_path, ["chatgpt.com", "openai.com"])
        if not cookies:
            continue

        # Look for NextAuth session token (single or chunked .0, .1)
        session_token = cookies.get("__Secure-next-auth.session-token", "")
        if not session_token:
            t0 = cookies.get("__Secure-next-auth.session-token.0", "")
            t1 = cookies.get("__Secure-next-auth.session-token.1", "")
            if t0:
                session_token = t0 + t1

        if not session_token:
            continue

        logger.info("[Browser Sync] Found ChatGPT session token in %s (%s)", b_name, db_path)

        # Build cookie header string
        cookie_header = "; ".join([f"{k}={v}" for k, v in cookies.items()])
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Cookie": cookie_header,
            "Accept": "*/*",
            "Referer": "https://chatgpt.com/",
            "Origin": "https://chatgpt.com",
        }

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get("https://chatgpt.com/api/auth/session", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    access_token = data.get("accessToken")
                    user = data.get("user") or {}
                    email = user.get("email") or "User"
                    expires = data.get("expires")

                    if access_token:
                        logger.info("[Browser Sync] Acquired fresh ChatGPT accessToken for %s (expires: %s)", email, expires)
                        return {
                            "ok": True,
                            "token": access_token,
                            "email": email,
                            "expires": expires,
                            "browser": b_name,
                            "db_path": db_path,
                            "name": f"ChatGPT Web ({email})",
                        }
                else:
                    last_err = f"ChatGPT /api/auth/session returned HTTP {resp.status_code}"
        except Exception as e:
            last_err = str(e)
            logger.debug("[Browser Sync] Failed querying /api/auth/session: %s", e)

    raise RuntimeError(last_err or "No active ChatGPT Web session found in any browser profile.")


async def extract_gemini_from_browser() -> dict[str, Any]:
    """
    Inspects installed browser profiles for Google account cookies (__Secure-1PSID, etc.)
    and verifies access to https://gemini.google.com/app.
    """
    db_list = find_browser_cookie_databases()
    for b_name, db_path in db_list:
        cookies = read_cookies_from_sqlite(db_path, ["google.com", "gemini.google.com"])
        if not cookies:
            continue

        psid = cookies.get("__Secure-1PSID") or cookies.get("SID")
        if not psid:
            continue

        cookie_str = "; ".join(
            [f"{k}={v}" for k, v in cookies.items() if k in (
                "__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-1PSIDCC", "SID", "HSID", "SSID", "APISID", "SAPISID", "COMPASS"
            )]
        )

        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Cookie": cookie_str,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                r = await client.get("https://gemini.google.com/app", headers=headers)
                if r.status_code == 200:
                    m_wiz = re.search(r"window\.WIZ_global_data\s*=\s*(\{.*?\});", r.text, re.DOTALL)
                    at_token = None
                    if m_wiz:
                        import json
                        wiz = json.loads(m_wiz.group(1))
                        at_token = wiz.get("SNlM0e")

                    return {
                        "ok": True,
                        "token": psid,
                        "at_token": at_token,
                        "cookies": cookies,
                        "browser": b_name,
                        "name": "Gemini Web (Browser Account)",
                    }
        except Exception as e:
            logger.debug("[Browser Sync] Gemini verification failed: %s", e)

    raise RuntimeError("No active Google Gemini session found in browser profiles.")


async def sync_all_web_providers_from_browser(provider_manager: Any = None) -> dict[str, Any]:
    """
    Auto-detects and synchronizes all possible web providers (ChatGPT Web, Gemini Web, DeepSeek Web)
    directly from local browser profiles, updating and persisting their credentials in ProviderManager.
    """
    results: dict[str, Any] = {}

    # 1. Sync ChatGPT Web
    try:
        cg_res = await extract_chatgpt_from_browser()
        if cg_res.get("ok") and cg_res.get("token"):
            if provider_manager:
                for pid in ("chatgpt-web", "chatgpt_web"):
                    cfg = provider_manager.providers.get(pid)
                    if cfg:
                        cfg.api_key = cg_res["token"]
                        cfg.enabled = True
                        if cg_res.get("name"):
                            cfg.name = cg_res["name"]
                        results[pid] = {
                            "status": "synced",
                            "name": cfg.name,
                            "email": cg_res.get("email"),
                            "browser": cg_res.get("browser"),
                        }
            else:
                results["chatgpt-web"] = cg_res
    except Exception as e:
        results["chatgpt-web"] = {"status": "skipped", "error": str(e)}

    # 2. Sync Gemini Web
    try:
        gem_res = await extract_gemini_from_browser()
        if gem_res.get("ok"):
            results["gemini-web"] = {
                "status": "detected",
                "browser": gem_res.get("browser"),
                "has_at_token": bool(gem_res.get("at_token")),
            }
    except Exception as e:
        results["gemini-web"] = {"status": "skipped", "error": str(e)}

    if provider_manager and any(r.get("status") == "synced" for r in results.values()):
        try:
            provider_manager.save()
            logger.info("[Browser Sync] Saved refreshed credentials to external_providers.json")
        except Exception as e:
            logger.error("[Browser Sync] Error saving provider manager config: %s", e)

    return {
        "status": "ok",
        "synced_at": time.time(),
        "providers": results,
    }
