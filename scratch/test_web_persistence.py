import asyncio
import json
import tempfile
from pathlib import Path

from agy_proxy.auth.pool import AccountPool
from agy_proxy.auth.gemini_web import GeminiWebSession
from agy_proxy.auth.api_key import AIStudioApiKeySession
from agy_proxy.auth.oauth import AntigravityOAuthSession

async def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        acc_file = tmp / "accounts.json"
        api_file = tmp / "api_keys.json"
        web_file = tmp / "web_sessions.json"

        pool = AccountPool(accounts_file=acc_file, api_keys_file=api_file, web_sessions_file=web_file)

        # Add OAuth
        oauth = AntigravityOAuthSession(
            account_id="acc_1",
            email="test@gmail.com",
            refresh_token="rt_123",
            access_token="ya29.123",
            is_primary=True,
            enabled=True,
            on_token_refreshed=pool.save_accounts,
        )
        pool.accounts["acc_1"] = oauth

        # Add API Key
        api = AIStudioApiKeySession(
            account_id="key_1",
            email="aistudio@api.google",
            api_key="AIzaSyTestKey123",
            enabled=True,
            on_token_refreshed=pool.save_accounts,
        )
        pool.accounts["key_1"] = api

        # Add Gemini Web
        web = GeminiWebSession(
            account_id="gw_1",
            name="Gemini Web",
            email="gemini-web@browser.local",
            cookies={"__Secure-1PSID": "test_psid_cookie"},
            enabled=True,
            on_token_refreshed=pool.save_accounts,
        )
        pool.accounts["gw_1"] = web

        pool.save_accounts()
        print("Initial save done.")
        print("web_sessions.json:", web_file.read_text())

        # Now disable API Key and Gemini Web
        pool.set_account_enabled("key_1", False)
        pool.set_account_enabled("gw_1", False)

        print("\nAfter disabling:")
        print("api_keys.json:", api_file.read_text())
        print("web_sessions.json:", web_file.read_text())

        # Now simulate proxy restart!
        print("\n--- RESTARTING PROXY ---")
        pool2 = AccountPool(accounts_file=acc_file, api_keys_file=api_file, web_sessions_file=web_file)
        pool2.load_accounts()

        print(f"After load_accounts():")
        for aid, a in pool2.accounts.items():
            print(f"  {aid} ({a.auth_method}): enabled={a.enabled}")

        await pool2.initialize_all()

        print(f"After initialize_all():")
        for aid, a in pool2.accounts.items():
            print(f"  {aid} ({a.auth_method}): enabled={a.enabled}")

        models = await pool2.get_pool_models()
        print(f"After get_pool_models():")
        for aid, a in pool2.accounts.items():
            print(f"  {aid} ({a.auth_method}): enabled={a.enabled}")

if __name__ == "__main__":
    asyncio.run(main())
