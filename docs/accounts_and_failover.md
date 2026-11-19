# 👥 Multi-Account Pool & Failover Architecture

`agy-proxy` allows you to pool multiple Google OAuth accounts and AI Studio API keys, automatically distributing traffic and failing over seamlessly across quota limits.

---

## ➕ Adding Accounts

### 1. Via Web Dashboard
Navigate to `http://127.0.0.1:8000` in your browser:
- Click **"Add Account"** -> **Google OAuth (PKCE)** to connect personal/work Google accounts with 1 click.
- Click **"Add API Key"** to add Google AI Studio keys (`AIzaSy...`).
- View real-time quota bars, requests count, and remaining tokens per account.

### 2. Via CLI
```bash
# Log in a standard consumer Google account via PKCE browser flow:
agy-proxy auth login

# Log in an enterprise / GCP Terms of Service (GCP ToS) Google account:
agy-proxy auth login --gcp-tos

# Add an AI Studio API key:
agy-proxy auth api "AIzaSy..." --name "My Work Key"

# List all accounts and remaining quotas:
agy-proxy auth list
```

---

## 📦 Account Pool Export & Import (Backup & Migration)

Safely back up your pooled accounts and migrate them across machines:

```bash
# Export all pooled accounts to a JSON backup file:
agy-proxy export-accounts -o my_accounts_backup.json

# Export to stdout (for piping or secret managers):
agy-proxy export-accounts

# Import accounts from file (merging with existing accounts):
agy-proxy import-accounts my_accounts_backup.json

# Import accounts replacing existing pool:
agy-proxy import-accounts my_accounts_backup.json --replace
```

---

## 🔁 Automatic Multi-Candidate Failover Architecture

When an account exhausts its rate limit or receives an upstream `429 Too Many Requests` or quota exhaustion error:
1. `agy-proxy` intercepts the error immediately.
2. Selects the next healthy account with available quota from the pool (using highest-remaining-quota strategy).
3. Seamlessly transparently re-executes the streaming request.
4. Client coding agents (Cursor, Claude Code, Cline, Aider) never crash or stall.

### Model Tier Routing & Account Selection (`v2.3.0+`)
- **Gemini 4 Hydrogen on Consumer OAuth**: `gemini-4-hydrogen` (`-low`, `-medium`, `-high`, `-xhigh`) is enabled by default across **all** standard consumer and GCP ToS OAuth accounts (separated from restricted enterprise Argon/Carbon quota-group checks).
- **Flexible Account Targeting**: Passing a specific account (`AGY_ACCOUNT`, `-a`, or `specific_account_id`) resolves cleanly by **email address**, **display name**, or **account ID**.
- **Reserve Guest Web Fallbacks**: Zero-auth guest providers (`gemini-web-guest`, `chatgpt-web-guest`) default to `enabled: false` as opt-in reserve fallbacks so your primary OAuth and API key pool is always prioritized. See [Web Providers Guide](web_providers.md).

---

## 🔒 Secure Credential Storage & Native Windows Support

By default, OAuth tokens and sensitive pool credentials are encrypted and stored via the operating system's native credential store:
- **Linux & macOS**: Stored securely via system `keyring` (Secret Service / KWallet / Keychain).
- **Windows**: Stored via native **Windows Credential Manager** (`advapi32.dll` ctypes integration). Even in standalone frozen executable builds without the `keyring` package, credentials are read and persisted with zero external dependencies.

---

## 🌐 Upstream Proxy Support (`proxy_url` & Environment Variables)

For corporate networks, geo-restricted countries, or custom routing, `agy-proxy` supports per-account proxy tunneling as well as global environment variables:

- **Per-Account Proxy**: Configure a custom `proxy_url` (e.g. `socks5://127.0.0.1:1080` or `http://proxy.corp:3128`) directly in the Web Dashboard or API. Saved permanently across sessions.
- **Global Environment Variables**: Automatically honors standard environment variables:
  - `AGY_UPSTREAM_PROXY`
  - `ALL_PROXY`
  - `HTTPS_PROXY` / `HTTP_PROXY`

---

## 🔄 Switching Active Antigravity CLI & Standalone Sessions

If you use Antigravity CLI (`agy`) or the Standalone Web Agent, you can switch their active authenticated identity on the fly.

### Via Web Dashboard
Click the **"Active Identity"** badge next to any account in the dashboard to switch the running Antigravity instance instantly.

### Via CLI
```bash
# List available OAuth accounts and active statuses:
agy-proxy switch --list

# Switch to a specific account for both CLI and Standalone:
agy-proxy switch "user@example.com"

# Switch to a specific account AND sync directly into Antigravity IDE SQLite DB:
agy-proxy switch "user@example.com" --sync-ide-db

# Switch an account ONLY for the CLI terminal:
agy-proxy switch "user@example.com" --cli

# Switch an account ONLY for the Standalone Web Agent:
agy-proxy switch "user@example.com" --standalone-only

# Automatically rotate to the account with the highest remaining quota:
agy-proxy switch --best
```

---

[← Back to Main README](../README.md)
