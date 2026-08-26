# 🌐 Cloudflare Edge Proxy & Serverless Guide

This directory contains everything you need to run **Antigravity Proxy** through **Cloudflare** for:
1. 🌍 **Geo-Bypass**: Bypass regional IP blocks on Google CloudCode & Gemini APIs with 0% VPN overhead.
2. ☁️ **100% Serverless Mode**: Run the proxy 24/7 on Cloudflare Edge without keeping your PC or server turned on.
3. 🔒 **Public HTTPS Access**: Connect from Cursor, Windsurf, Claude Code, or mobile devices anywhere via Cloudflare Tunnels or Worker URLs.

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

In this mode, Cloudflare Worker runs the entire proxy logic and token refreshes in the cloud.

#### 1. Upload your accounts to Cloudflare Secrets
Run this single command from your local machine to upload your existing account pool:
```bash
cd cloudflare
npx wrangler secret put ACCOUNTS_JSON < ~/.config/agy-proxy/accounts.json
```

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
- **Base URL**: `https://agy-proxy-edge.<your-subdomain>.workers.dev/v1`
- **API Key**: `dummy` (or your `PROXY_API_KEY`)
- **Model**: `gemini-3.7-flash-high`, `claude-3-7-sonnet`, `gpt-4o`

---

## 🚇 Quick Public HTTPS Tunnel (`cloudflared`)

If you run `agy-proxy` locally and just want a quick public HTTPS URL without deploying anything:

```bash
# Start an instant tunnel
cloudflared tunnel --url http://127.0.0.1:8000
```

`cloudflared` will generate a public URL like:
`https://random-name.trycloudflare.com`

You can use `https://random-name.trycloudflare.com/v1` in Cursor, Windsurf, or Claude Code on any external device!

---

## 📋 Environment Variables Reference

| Variable | Scope | Description |
|---|---|---|
| `CLOUDFLARE_UPSTREAM_URL` | Local `agy-proxy` | URL of your deployed Cloudflare Worker to route traffic through. |
| `ACCOUNTS_JSON` | Cloudflare Worker Secret | JSON string of account credentials for 100% serverless edge mode. |
| `PROXY_API_KEY` | Local or Worker | Secret key required in client request headers (`Authorization: Bearer <key>`). |
| `HTTP_PROXY` / `HTTPS_PROXY` | Local `agy-proxy` | Standard proxy URL for Python `httpx`. |
