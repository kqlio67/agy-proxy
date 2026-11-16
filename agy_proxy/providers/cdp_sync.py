"""
CDP (Chrome DevTools Protocol) session extraction for Web Providers.
Extracts live session tokens, cookies, and local storage values from a running
Chrome browser with --remote-debugging-port (default: 9222).

Supports:
- ChatGPT Web (chatgpt.com NextAuth session-token -> Bearer accessToken)
- DeepSeek Web (chat.deepseek.com localStorage userToken)
- Claude Web (claude.ai sessionKey cookie)
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from typing import Any

import httpx
import websockets

logger = logging.getLogger("agy_proxy.providers.cdp_sync")

DEFAULT_CDP_PORT = 9222
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)


async def get_browser_ws_url(cdp_port: int = DEFAULT_CDP_PORT) -> str:
    """Connects to http://127.0.0.1:<cdp_port>/json/version and returns the webSocketDebuggerUrl."""
    loop = asyncio.get_running_loop()
    version_url = f"http://127.0.0.1:{cdp_port}/json/version"

    def _fetch():
        with urllib.request.urlopen(version_url, timeout=3) as resp:
            return json.loads(resp.read().decode())

    try:
        data = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        raise RuntimeError(
            f"Cannot connect to Chrome CDP at port {cdp_port}. "
            f"Ensure Chrome is running with --remote-debugging-port={cdp_port}."
        ) from e

    ws_url = data.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError(f"Chrome CDP at port {cdp_port} did not return webSocketDebuggerUrl.")
    return ws_url


async def get_all_cookies(browser_ws_url: str) -> list[dict[str, Any]]:
    """Retrieves all browser cookies via Storage.getCookies on the browser WebSocket."""
    async with websockets.connect(browser_ws_url, ping_interval=None) as ws:
        msg = json.dumps({"id": 1, "method": "Storage.getCookies"})
        await ws.send(msg)
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        res = json.loads(raw)
        return res.get("result", {}).get("cookies", [])


async def extract_chatgpt_via_cdp(cdp_port: int = DEFAULT_CDP_PORT) -> dict[str, Any]:
    """
    Extracts ChatGPT Web session token / accessToken from Chrome CDP:
    1. Checks open tabs for chatgpt.com to read live /api/auth/session
    2. Fallback: reads Storage.getCookies for __Secure-next-auth.session-token
    3. Calls /api/auth/session to acquire fresh Bearer accessToken and user email.
    """
    ws_url = await get_browser_ws_url(cdp_port)

    # 1. Check open tabs
    loop = asyncio.get_running_loop()
    tabs_url = f"http://127.0.0.1:{cdp_port}/json"

    def _fetch_tabs():
        try:
            with urllib.request.urlopen(tabs_url, timeout=3) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return []

    tabs = await loop.run_in_executor(None, _fetch_tabs)
    chatgpt_tab = next(
        (t for t in tabs if "chatgpt.com" in t.get("url", "") and t.get("webSocketDebuggerUrl")),
        None,
    )

    if chatgpt_tab:
        try:
            tab_ws_url = chatgpt_tab["webSocketDebuggerUrl"]
            async with websockets.connect(tab_ws_url, ping_interval=None) as tab_ws:
                expr = (
                    "(async () => {"
                    "  try {"
                    "    const res = await fetch('/api/auth/session');"
                    "    const d = await res.json();"
                    "    return JSON.stringify({ ok: true, accessToken: d.accessToken, user: d.user });"
                    "  } catch (e) {"
                    "    return JSON.stringify({ ok: false, error: String(e) });"
                    "  }"
                    "})()"
                )
                msg = json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expr, "awaitPromise": True, "returnByValue": True},
                })
                await tab_ws.send(msg)
                raw = await asyncio.wait_for(tab_ws.recv(), timeout=8.0)
                res = json.loads(raw)
                val_str = res.get("result", {}).get("result", {}).get("value")
                if val_str:
                    val = json.loads(val_str)
                    if val.get("ok") and val.get("accessToken"):
                        email = val.get("user", {}).get("email")
                        return {
                            "ok": True,
                            "token": val["accessToken"],
                            "email": email,
                            "user_id": val.get("user", {}).get("id"),
                            "name": f"ChatGPT Web ({email})" if email else "ChatGPT Web (Auth)",
                            "source": "live_tab",
                        }
        except Exception as e:
            logger.debug("[CDP ChatGPT] Direct tab session fetch failed: %s", e)

    # 2. Fallback: extract cookie from Storage.getCookies
    cookies = await get_all_cookies(ws_url)
    cookie_dict: dict[str, str] = {}
    for c in cookies:
        domain = c.get("domain", "")
        if "chatgpt.com" in domain or "openai.com" in domain:
            cookie_dict[c.get("name", "")] = c.get("value", "")

    # Handle chunked cookies (__Secure-next-auth.session-token.0, .1)
    session_token = cookie_dict.get("__Secure-next-auth.session-token", "")
    if not session_token:
        parts = []
        for i in range(15):
            k = f"__Secure-next-auth.session-token.{i}"
            if k in cookie_dict:
                parts.append(cookie_dict[k])
            else:
                break
        session_token = "".join(parts)

    if not session_token:
        raise RuntimeError(
            "Could not find ChatGPT session token in Chrome. "
            "Please ensure you are logged into chatgpt.com in Chrome."
        )

    # 3. Call /api/auth/session using session_token cookie to fetch bearer accessToken
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Cookie": f"__Secure-next-auth.session-token={session_token}",
        "Accept": "application/json",
        "Referer": "https://chatgpt.com/",
    }
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.get("https://chatgpt.com/api/auth/session", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            access_tok = data.get("accessToken")
            user = data.get("user") or {}
            email = user.get("email")
            if access_tok:
                return {
                    "ok": True,
                    "token": access_tok,
                    "email": email,
                    "user_id": user.get("id"),
                    "name": f"ChatGPT Web ({email})" if email else "ChatGPT Web (Auth)",
                    "source": "session_cookie_exchange",
                }

    # If endpoint didn't exchange bearer, return raw session_token
    return {
        "ok": True,
        "token": session_token,
        "email": None,
        "name": "ChatGPT Web (Session Cookie)",
        "source": "raw_session_token",
    }


async def extract_deepseek_via_cdp(cdp_port: int = DEFAULT_CDP_PORT) -> dict[str, Any]:
    """
    Extracts DeepSeek Web userToken from Chrome CDP:
    1. Checks if open tab for chat.deepseek.com exists
    2. If not, temporarily opens a background target to chat.deepseek.com
    3. Evaluates localStorage.getItem('userToken')
    4. Validates token via /api/v0/users/current to verify account email/name.
    """
    ws_url = await get_browser_ws_url(cdp_port)
    loop = asyncio.get_running_loop()
    tabs_url = f"http://127.0.0.1:{cdp_port}/json"

    def _fetch_tabs():
        try:
            with urllib.request.urlopen(tabs_url, timeout=3) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return []

    tabs = await loop.run_in_executor(None, _fetch_tabs)
    ds_tab = next(
        (t for t in tabs if "deepseek.com" in t.get("url", "") and t.get("webSocketDebuggerUrl")),
        None,
    )

    created_target_id = None
    tab_ws_url = None

    if ds_tab:
        tab_ws_url = ds_tab["webSocketDebuggerUrl"]
    else:
        # Create ephemeral background target to chat.deepseek.com
        async with websockets.connect(ws_url, ping_interval=None) as browser_ws:
            msg = json.dumps({
                "id": 1,
                "method": "Target.createTarget",
                "params": {"url": "https://chat.deepseek.com", "background": True},
            })
            await browser_ws.send(msg)
            res = json.loads(await asyncio.wait_for(browser_ws.recv(), timeout=5.0))
            created_target_id = res.get("result", {}).get("targetId")

        if not created_target_id:
            raise RuntimeError("Failed to create background tab to read DeepSeek storage.")

        await asyncio.sleep(1.8)
        tabs_after = await loop.run_in_executor(None, _fetch_tabs)
        tab_obj = next((t for t in tabs_after if t.get("id") == created_target_id), None)
        if tab_obj:
            tab_ws_url = tab_obj.get("webSocketDebuggerUrl")

    if not tab_ws_url:
        raise RuntimeError("Could not establish debugger connection to DeepSeek tab.")

    raw_val_str = None
    try:
        async with websockets.connect(tab_ws_url, ping_interval=None) as tab_ws:
            expr = (
                "(() => {"
                "  let tok = localStorage.getItem('userToken');"
                "  if (!tok) {"
                "    for (let i = 0; i < localStorage.length; i++) {"
                "      const k = localStorage.key(i);"
                "      if (k && (k.includes('token') || k.includes('user'))) {"
                "        const v = localStorage.getItem(k);"
                "        if (v && v.length > 20) { tok = v; break; }"
                "      }"
                "    }"
                "  }"
                "  let u = null;"
                "  try { u = JSON.parse(localStorage.getItem('user') || localStorage.getItem('userInfo')); } catch(e) {}"
                "  return JSON.stringify({ userToken: tok, user: u });"
                "})()"
            )
            msg = json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": expr, "returnByValue": True},
            })
            await tab_ws.send(msg)
            raw = await asyncio.wait_for(tab_ws.recv(), timeout=8.0)
            res = json.loads(raw)
            raw_val_str = res.get("result", {}).get("result", {}).get("value")
    finally:
        # Clean up ephemeral target if created
        if created_target_id:
            try:
                async with websockets.connect(ws_url, ping_interval=None) as browser_ws:
                    await browser_ws.send(json.dumps({
                        "id": 2,
                        "method": "Target.closeTarget",
                        "params": {"targetId": created_target_id},
                    }))
                    await asyncio.wait_for(browser_ws.recv(), timeout=3.0)
            except Exception:
                pass

    if not raw_val_str:
        raise RuntimeError("No response from DeepSeek page when reading localStorage.")

    parsed = json.loads(raw_val_str)
    user_token_raw = parsed.get("userToken")
    if not user_token_raw:
        raise RuntimeError(
            "Could not find userToken in DeepSeek localStorage. "
            "Please ensure you are logged into chat.deepseek.com in Chrome."
        )

    # userToken can be stored as JSON: {"value": "BDW/...", "__version": "0"}
    clean_token = user_token_raw
    if isinstance(user_token_raw, str) and user_token_raw.startswith("{"):
        try:
            tok_obj = json.loads(user_token_raw)
            if "value" in tok_obj:
                clean_token = tok_obj["value"]
        except Exception:
            pass

    # Verify and get user info
    email = None
    username = None
    try:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Authorization": f"Bearer {clean_token}",
            "Accept": "application/json",
            "Referer": "https://chat.deepseek.com/",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://chat.deepseek.com/api/v0/users/current", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    u_data = data.get("data") or {}
                    email = u_data.get("email") or u_data.get("mobile")
                    username = u_data.get("name")
    except Exception as e:
        logger.debug("[CDP DeepSeek] Verification endpoint failed: %s", e)

    disp_name = email or username
    return {
        "ok": True,
        "token": clean_token,
        "email": email,
        "username": username,
        "name": f"DeepSeek Web ({disp_name})" if disp_name else "DeepSeek Web (Auth)",
    }


async def extract_claude_via_cdp(cdp_port: int = DEFAULT_CDP_PORT) -> dict[str, Any]:
    """Extracts sessionKey from claude.ai cookies via Chrome CDP."""
    ws_url = await get_browser_ws_url(cdp_port)
    cookies = await get_all_cookies(ws_url)
    session_key = None
    for c in cookies:
        domain = c.get("domain", "")
        name = c.get("name", "")
        if "claude.ai" in domain and (name == "sessionKey" or name.startswith("sk-ant-sid")):
            session_key = c.get("value")
            break

    if not session_key:
        raise RuntimeError("Could not find sessionKey cookie for claude.ai in Chrome.")

    return {
        "ok": True,
        "token": session_key,
        "name": "Claude.ai Web Session",
    }


async def sync_provider_via_cdp(
    provider_id: str,
    cdp_port: int = DEFAULT_CDP_PORT,
    provider_manager: Any = None,
) -> dict[str, Any]:
    """
    Auto-detects the provider type, extracts session credentials via Chrome CDP
    (falling back automatically to direct browser SQLite profile inspection if CDP is inactive),
    updates the provider configuration in ProviderManager, and persists it.
    """
    pid = provider_id.lower()
    result = None
    cdp_error = None

    try:
        if "chatgpt" in pid or "openai_web" in pid:
            result = await extract_chatgpt_via_cdp(cdp_port)
        elif "deepseek" in pid:
            result = await extract_deepseek_via_cdp(cdp_port)
        elif "claude" in pid or "anthropic_web" in pid:
            result = await extract_claude_via_cdp(cdp_port)
        else:
            raise ValueError(f"CDP session extraction not supported for provider '{provider_id}'.")
    except Exception as err:
        cdp_error = err
        logger.info("[CDP Sync] CDP extraction on port %s failed (%s), attempting direct browser profile sync...", cdp_port, err)

    if not result or not result.get("token"):
        # Fallback to direct browser profile sync
        try:
            from agy_proxy.providers.browser_cookie_sync import extract_chatgpt_from_browser, extract_gemini_from_browser
            if "chatgpt" in pid or "openai_web" in pid:
                result = await extract_chatgpt_from_browser()
            elif "gemini" in pid:
                result = await extract_gemini_from_browser()
        except Exception as b_err:
            logger.debug("[CDP Sync] Browser profile fallback also failed: %s", b_err)

    if not result or not result.get("token"):
        err_msg = str(cdp_error or "Could not extract credentials via CDP or local browser profiles.")
        raise RuntimeError(err_msg)

    token = result["token"]

    if provider_manager:
        cfg = provider_manager.providers.get(provider_id)
        if not cfg:
            # Check without hyphen
            alt_id = provider_id.replace("_", "-")
            cfg = provider_manager.providers.get(alt_id)
            if cfg:
                provider_id = alt_id

        if cfg:
            cfg.api_key = token
            cfg.enabled = True
            if result.get("name"):
                cfg.name = result["name"]
            provider_manager.save()
            logger.info("Successfully updated provider '%s' via CDP", provider_id)

    return {
        "status": "ok",
        "provider_id": provider_id,
        "provider_name": result.get("name") or provider_id,
        "email": result.get("email"),
        "token_preview": f"{token[:8]}...{token[-4:]}" if len(token) > 16 else "***",
        "details": result,
    }
