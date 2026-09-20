import asyncio
import json
import tempfile
from pathlib import Path

from agy_proxy.auth.pool import AccountPool

async def test_migration():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        acc_file = tmp / "accounts.json"
        api_file = tmp / "api_keys.json"
        web_file = tmp / "web_sessions.json"

        # Suppose web_sessions.json has a paused session
        web_file.write_text(json.dumps({
            "web_sessions": [{
                "account_id": "gw_123",
                "name": "Gemini Web",
                "email": "gemini-web@browser.local",
                "auth_method": "gemini_web",
                "cookies": {"__Secure-1PSID": "cookie123"},
                "enabled": False,
            }]
        }))

        # And accounts.json has an old entry or re-imported entry
        acc_file.write_text(json.dumps({
            "accounts": [
                {
                    "account_id": "gw_old",
                    "name": "Gemini Web",
                    "email": "gemini-web@browser.local",
                    "auth_method": "gemini_web",
                    "cookies": {"__Secure-1PSID": "cookie123"},
                    "enabled": True,
                }
            ]
        }))

        pool = AccountPool(accounts_file=acc_file, api_keys_file=api_file, web_sessions_file=web_file)
        pool.load_accounts()

        for aid, a in pool.accounts.items():
            print(f"Account {aid}: enabled={a.enabled}")

        print("\nweb_sessions.json after load:")
        print(web_file.read_text())

if __name__ == "__main__":
    asyncio.run(test_migration())
