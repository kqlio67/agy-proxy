# 🌐 Cloudflare Edge Proxy & Serverless Guide

This directory contains everything you need to run **Antigravity Proxy** through **Cloudflare** for:
1. 🌍 **Geo-Bypass**: Bypass regional IP blocks on Google CloudCode & Gemini APIs with 0% VPN overhead.
2. ☁️ **100% Serverless Mode**: Run the proxy 24/7 on Cloudflare Edge without keeping your PC or server turned on.
3. 🔒 **Public HTTPS Access**: Connect from Cursor, Windsurf, Claude Code, Cline, Roo Code, or mobile devices anywhere via Cloudflare Tunnels or Worker URLs.

---

## ⚡ Architecture Modes

You can use Cloudflare in two ways:

| Mode | Where Accounts are Stored | Requires Running Local PC? | Best For |
|---|---|---|---|
| **Mode 1: Upstream Gateway (Hybrid)** | `~/.config/agy-proxy/accounts.json` on local PC | Yes | Local development with Web Dashboard, automatic local token management |
| **Mode 2: 100% Serverless Edge** | `ACCOUNTS_JSON` secret in Cloudflare | **No** (runs 24/7 in the cloud) | Team sharing, accessing from multiple laptops/phones with zero local server |

---

## 🚀 Setup Guide

### Mode 1: Upstream Gateway / Geo-Bypass (Recommended for local dev)

In this mode, `agy-proxy` runs on your computer and routes all outgoing Google API traffic through your Cloudflare Worker to bypass regional blocks.

#### 1. Deploy the Worker
```bash
cd cloudflare
npx wrangler deploy
```
*Wrangler will output your worker URL, e.g.: `https://agy-proxy-edge.<your-subdomain>.workers.dev`*

#### 2. Start `agy-proxy` with the Worker URL
```bash
# Using CLI flag:
agy-proxy --port 8000 --cloudflare-url "https://agy-proxy-edge.<your-subdomain>.workers.dev"

# OR via environment variable:
export CLOUDFLARE_UPSTREAM_URL="https://agy-proxy-edge.<your-subdomain>.workers.dev"
agy-proxy --port 8000
```

---

### Mode 2: 100% Serverless Standalone Mode (No PC needed)

In this mode, the Cloudflare Worker runs the entire proxy logic, token refreshes, and multi-account failover directly in the cloud.

#### 1. Upload your accounts to Cloudflare Secrets
Run this single command from your local machine to upload your existing account pool:
```bash
cd cloudflare
npx wrangler secret put ACCOUNTS_JSON < ~/.config/agy-proxy/accounts.json
```
*(Supports standard `accounts.json`, `api_keys.json`, or export files from `/api/accounts/export`)*

#### 2. (Optional) Set an API Key for security
```bash
npx wrangler secret put PROXY_API_KEY
# Enter your secret API key when prompted
```

#### 3. Deploy
```bash
npx wrangler deploy
```

#### 4. Use your Serverless URL in your IDE / CLI
You can now directly use:
- **OpenAI Base URL**: `https://agy-proxy-edge.<your-subdomain>.workers.dev/v1`
- **Anthropic Base URL**: `https://agy-proxy-edge.<your-subdomain>.workers.dev`
- **API Key**: `dummy` (or your `PROXY_API_KEY`)
- **Default Model**: `gemini-3.8-flash-high`
- **Supported Models**:
  - `gemini-3.8-flash-high`, `gemini-3.8-flash-medium`, `gemini-3.8-flash-low`
  - `gemini-3.7-flash-high`, `gemini-3.7-flash-medium`, `gemini-3.7-flash-low`
  - `gemini-3.1-pro-high`, `gemini-3.1-pro-low`
  - `gemini-3.1-flash-lite` (`gpt-4o-mini`)
  - `claude-sonnet-5-5-high`, `claude-sonnet-5-5-medium`, `claude-sonnet-5-5-low`
  - `claude-opus-5-5-high`, `claude-opus-5-5-medium`, `claude-opus-5-5-low`
  - `claude-sonnet-4-6` (`claude-3-7-sonnet`, `claude-3-5-sonnet`)
  - `claude-opus-4-6-thinking` (`claude-3-opus`)
  - `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.4`, `gpt-oss-120b-medium`, `gpt-4o`, `o1`, `deepseek-r1`

---

## 💡 Key Features in v2.26.0

- **Upstream User-Agent Alignment**: Uses `antigravity/hub/2.26.0` (cl=1005016104) matching official client telemetry.
- **Claude 5.5 Support & Auto-Fallback**: Full support for Claude Sonnet 5.5 & Opus 5.5 with 1M token context, adaptive reasoning, and automatic account fallback to Claude 4.6 on 404/429.
- **DeepAgent & Experiments Activation**: Automatically activates `enable-deepagent: true` on `/v1internal:listExperiments` pass-through for autonomous subagent workflows (`/deepagent`).
- **Claude Code Billing Header Stripping**: Strips internal `x-anthropic-billing-header:` prompts to eliminate Google CloudCode 429 RESOURCE_EXHAUSTED rejections.
- **Smart 429 Cooldown**: Dynamically parses reset duration from 429 responses (`resets in ...` / `retry after ...`) with intelligent 15s default cooldown.
- **Multi-Account Failover Loop**: Automatically iterates through pooled accounts on `429 Too Many Requests`, `401 Unauthorized` (with automatic token refresh), or `5xx Server Error` (500, 502, 503, 504), preventing interrupted requests.
- **Role Sanitization for Claude Models**: Message roles are strictly sanitized to lowercase `user` (for `user`, `context`) or `model` (for `model`, `assistant`), and system instructions are extracted to `systemInstruction`, preventing CloudCode Error 500 when using Claude Sonnet or Claude Opus.
- **Roo Code & Cline Optimization**:
  - Full tool conversion (`tools`, `tool_use`, `tool_result`, `functionCall`, `functionResponse`).
  - Supports `custom.input_schema`, `custom.parameters`, `custom.name`, and `custom.description`.
  - Advanced schema sanitization (`sanitizeGeminiSchema`): flattens `anyOf`, `oneOf`, `allOf`, converts nullable types (`["string", "null"]` -> `type: "string", nullable: true`), and strips unsupported metadata (`$schema`, `definitions`, `additionalProperties`) to prevent Gemini 400 errors.
- **Claude Code CLI Compatibility**: Full endpoint support for Claude Code CLI bootstrap (`/api/hello`, `/api/claude_cli/bootstrap`, `/api/claude_code_penguin_mode`, `/api/web/domain_info`, `/v1/messages`).
- **OpenAI Responses API**: Native compatibility with OpenAI Codex CLI (`/v1/responses`) and Chat Completions (`/v1/chat/completions`).
- **Disabled Accounts Filter**: Automatically skips accounts marked `enabled: false` during pool initialization and rotation.

---

## 🚇 Quick Public HTTPS Tunnel (`cloudflared`)

If you run `agy-proxy` locally and just want a quick public HTTPS URL without deploying anything:

```bash
# Start an instant tunnel
cloudflared tunnel --url http://127.0.0.1:8000
```

`cloudflared` will generate a public URL like:
`https://random-name.trycloudflare.com`

You can use `https://random-name.trycloudflare.com/v1` in Cursor, Windsurf, Claude Code, Cline, or Roo Code on any external device!

---

## 📋 Environment Variables Reference

| Variable | Scope | Description |
|---|---|---|
| `CLOUDFLARE_UPSTREAM_URL` | Local `agy-proxy` | URL of your deployed Cloudflare Worker to route traffic through for Geo-Bypass. |
| `ACCOUNTS_JSON` | Cloudflare Worker Secret | JSON string of account credentials for 100% serverless edge mode. |
| `PROXY_API_KEY` | Local or Worker | Secret key required in client request headers (`Authorization: Bearer <key>`). |
| `AGY_UPSTREAM_PROXY` / `ALL_PROXY` | Local `agy-proxy` | Corporate/SOCKS/HTTP proxy URL applied to all upstream Google API sessions. |
| `HTTP_PROXY` / `HTTPS_PROXY` | Local `agy-proxy` | Standard HTTP/HTTPS proxy URL for Python `httpx` client sessions. |
