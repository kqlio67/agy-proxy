import json, base64
from pathlib import Path
from agy_proxy.auth import AccountPool
from agy_proxy.switcher import resolve_antigravity_destinations

def get_active_antigravity_accounts(pool):
    cli_path = resolve_antigravity_destinations("cli")[0]
    ide_path = resolve_antigravity_destinations("ide")[0]

    def resolve_account_from_file(token_path: Path):
        if not token_path or not token_path.is_file() or token_path.stat().st_size == 0:
            return None
        try:
            with open(token_path) as f:
                tok_data = json.load(f)
            file_refresh = (tok_data.get("token") or {}).get("refresh_token", "").strip()
            
            # Match by refresh token
            if file_refresh:
                for a in pool.accounts.values():
                    if a.auth_method == "consumer" and a.refresh_token == file_refresh:
                        return a
        except Exception:
            pass
        return None

    cli_acc = resolve_account_from_file(cli_path)
    ide_acc = resolve_account_from_file(ide_path)
    return cli_acc, ide_acc

pool = AccountPool()
pool.load_accounts()
cli, ide = get_active_antigravity_accounts(pool)
print("CLI:", cli.email if cli else None)
print("IDE:", ide.email if ide else None)
