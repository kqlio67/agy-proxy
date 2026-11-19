# 🌐 Cloudflare Edge Worker & Remote Access

Run `agy-proxy` remotely, bypass regional restrictions (Geo-Bypass), or host a 100% serverless edge instance on Cloudflare Workers.

---

## ⚡ Mode 1: Geo-Bypass Upstream Gateway (Recommended)

If your local IP is blocked from accessing Google CloudCode or Generative Language APIs:

1. Deploy the worker to your Cloudflare account in 1 command:
   ```bash
   cd cloudflare
   npm install && npx wrangler deploy
   ```
2. Start your local `agy-proxy` pointing through the Cloudflare worker:
   ```bash
   agy-proxy --upstream-proxy "https://my-worker.my-account.workers.dev"
   ```
   Or set the environment variable:
   ```bash
   export AGY_UPSTREAM_PROXY="https://my-worker.my-account.workers.dev"
   ```

---

## ☁️ Mode 2: 100% Serverless Edge Mode (24/7 Cloud)

Run `agy-proxy` entirely on Cloudflare Workers without keeping your PC powered on:

1. Configure your pooled accounts into Cloudflare Secrets:
   ```bash
   agy-proxy export-accounts | npx wrangler secret put ACCOUNTS_JSON
   ```
2. Deploy the serverless edge worker:
   ```bash
   npx wrangler deploy
   ```
3. Point your IDE or Claude Code directly to your Worker endpoint:
   ```
   https://my-worker.my-account.workers.dev/v1
   ```

---

## 🔒 Quick Instant HTTPS Tunnel (`cloudflared`)

Expose your local `agy-proxy` to the internet with secure SSL in 1 command without opening ports:

```bash
# Start Cloudflare Tunnel:
cloudflared tunnel --url http://localhost:8000
```

Use the generated temporary URL (e.g. `https://<random>.trycloudflare.com/v1`) in your mobile devices, remote servers, or external clients.

---

## ✨ Edge Worker Parity (`v2.33.0` Sync)

The Cloudflare Worker ([`cloudflare/worker.js`](../cloudflare/worker.js)) stays synchronized with the local Python proxy:
- **Antigravity Hub v2.33.0 Telemetry**: Aligns upstream `User-Agent` to `antigravity/hub/2.33.0` (`cl=1023160026`) and activates curated v2.33.0 experiments.
- **Full Gemini 4 & Claude 5.5 Catalog**: Supports Gemini 4 Neon (`-low`/`-medium`/`-high`/`-xhigh`, `gemini-pro-orchestrator-neon`), Gemini 4 Hydrogen, Gemini 4 Carbon, Gemini 4 Argon, Web/Spark aliases, and Claude 5.5/4.6 failover.
- **Default Prompt Restriction Stripping & Guidelines Curation**: Automatically cleans `systemInstruction` and guideline-injected `contents` on `streamGenerateContent` and `generateContent`, and curates `template__system_prompts__guidelines` in `fetchAvailableModels`.

👉 *Detailed worker configuration & model list: [Cloudflare Edge README](../cloudflare/README.md)*

---

[← Back to Main README](../README.md)
