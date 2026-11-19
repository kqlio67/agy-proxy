# ⚡ Antigravity Proxy (`agy-proxy`)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](docs/deployment_and_services.md#docker--docker-compose)
[![Termux](https://img.shields.io/badge/Android-Termux-green.svg)](docs/deployment_and_services.md#android-via-termux)
[![Cloudflare Workers](https://img.shields.io/badge/Cloudflare-Workers-orange.svg)](docs/cloudflare_and_remote.md)

**Antigravity Proxy** (`agy-proxy`) is a high-performance local AI gateway that supercharges the official **Google Antigravity 2.0 (v2.33.0+)** ecosystem.

It unlocks next-gen models (**Gemini 4 Neon**, **Orchestrator Neon**, **Gemini 4 Hydrogen**, **Gemini 4 Carbon**, **Gemini 4 Argon**, **Gemini 3.8 Flash**, **Claude Sonnet 4.6 / 5.5**, **Claude Opus 4.6 / 5.5**) plus unified **Web Providers** (**Gemini Web & Spark Agent**, **DeepSeek Web**, **ChatGPT Web**) through standard **OpenAI**, **Anthropic**, and **Google GenAI** API endpoints — complete with multi-account pooling, automated failover, default prompt restriction stripping, token-saving context compaction, and zero-Electron RAM optimization.

---

## 🌟 Why agy-proxy?

- 🆓 **Zero-Cost High-Tier AI**: Access Gemini 4 (Neon, Hydrogen, Carbon, Argon), Gemini 3.8, Claude, and web-backed models (Gemini Web/Spark, DeepSeek Web, ChatGPT Web) without paid API subscriptions.
- 🔄 **Universal API Compatibility**: Drop-in compatible with standard OpenAI (`/v1/chat/completions`, `/v1/responses`), Anthropic (`/v1/messages`), and Gemini CloudCode endpoints — including full streaming tool calling (`DSML` / `<tool_call>`) on web providers.
- 👥 **Multi-Account Pool & Auto-Failover**: Pool multiple Google OAuth accounts (consumer & GCP ToS), AI Studio keys, and web sessions; if one hits rate limits, traffic rotates seamlessly without crashing your IDE.
- 🚀 **Antigravity Web Hub (Saves RAM)**: Run the full Antigravity 2.0 interface directly in your browser without the heavy Electron wrapper, with v2.33.0 flags unlocked and dynamic server models preserved.
- 🧹 **Clean System Prompts by Default**: Automatically strips artificial prompt restrictions (CAPTCHA/bot-check bans, reverse-engineering bans, boilerplate guardrails) and curates `fetchAvailableModels` guidelines across CloudCode, Web Providers, and Cloudflare Workers.
- 🗜 **Context Compactor (Token Saver)**: Auto-compresses long chat histories using fast flash-lite models, preventing Google's 1M token limit and saving 80%–95% tokens.
- 🖥️ **1-Click IDE & CLI Integrations**: Native launch scripts and configurations for Antigravity IDE, `agy` CLI & ACP, Claude Code CLI, Cursor, Windsurf, VS Code (Continue, Roo Code, Cline), Codex CLI, and Aider.

---

## ⚡ Quick Start (Under 1 Minute)

### 1. One-Line Fast Install *(with live terminal progress bar)*

#### Linux & macOS:
```bash
curl -fsSL https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.sh | bash
```

#### Windows (PowerShell):
```powershell
irm https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.ps1 | iex
```

### 2. Run with Python / UV / Pip

```bash
# Clone the repository:
git clone https://github.com/kqlio67/agy-proxy.git
cd agy-proxy

# Run with UV (Recommended - no manual venv needed):
uv run python -m agy_proxy.cli start

# OR on Windows (1-Click Batch):
start_proxy.bat

# OR install in editable mode and run:
pip install -e .
agy-proxy start
```

### 3. Standalone Executable (Windows / Linux / macOS)

Run without needing Python or manual virtual environment setup:
- Download pre-built standalone binaries from **[GitHub Releases](https://github.com/kqlio67/agy-proxy/releases)** (e.g. `agy-proxy-windows-amd64.zip` for Windows).
- **Build locally on Windows**: Double-click `build_exe.bat` or run:
  ```cmd
  pip install pyinstaller
  python build_binary.py
  ```
  The standalone binary will be built in `dist\agy-proxy.exe`.

### 4. Open the Web Dashboard

Open **`http://127.0.0.1:8000`** in your browser to:
- Connect Google accounts via 1-click OAuth (PKCE) or add AI Studio API keys.
- View real-time quotas, token analytics, and request metrics.
- Configure context compactor thresholds and proxy tunneling.

---

## 🎯 Quick Launch Helpers (The 3 Main Workflows)

`agy-proxy` comes with dedicated, ready-to-run helpers for the most popular workflows:

### 1. 🚀 Antigravity Web Hub (Zero Electron)
Run Google Antigravity 2.0 in your browser, saving RAM (works even on headless VPS servers):
```bash
# Standard Web Hub (RAM freed, no Electron):
agy-proxy web

# Patched Web Hub (Unlocks Web Terminal, File Explorer, Battle Mode):
agy-proxy web --patched

# Open browser explicitly (-o) and clean up Electron (-k):
agy-proxy web --patched -k -o

# Headless server setup (download official binary without Electron):
agy-proxy web --download-server
```

👉 *Detailed guide: [Antigravity Web Mode & Patched Hub](docs/web_mode.md)*

### 2. 🤖 Claude Code CLI (Proxy or Direct Vanilla)
Run Anthropic's official Claude Code CLI with 1M Gemini context or directly with official Anthropic API:
```bash
# Launch with Gemini 1M context via proxy:
agy-proxy claude

# Launch directly with official Anthropic API (debloated, zero telemetry):
agy-proxy claude --direct

# Patch binary to remove prompt restrictions and debloat tips:
agy-proxy patch-claude apply

# Autonomous execution with selective MCP tools:
agy-proxy claude --danger --mcp rea
```
👉 *Detailed guide: [Claude Code CLI Integration & Patcher](docs/claude_code.md)*

### 3. ⌨️ OpenAI Codex CLI
Run OpenAI Codex CLI with full Antigravity model catalog:
```bash
# Launch interactive session:
agy-proxy codex

# Launch with specific model:
agy-proxy codex -m claude-sonnet-4-6
```
👉 *Detailed guide: [OpenAI Codex CLI Integration](docs/codex_cli.md)*

### 4. 💻 Google Antigravity CLI (`agy` v1.5.5) & ACP Server
Run the official Google Antigravity CLI coding agent or start the ACP stdio server for Zed & Neovim:
```bash
# Autonomous mode with proxy routing:
agy-proxy agy --mode auto

# Specific account with maximum reasoning effort and auto-approved tools:
agy-proxy agy -a user@example.com --mode auto --effort max --danger

# Start Agent Client Protocol (ACP) stdio server for Zed / Neovim:
agy-proxy acp --danger
```
👉 *Detailed guide: [Antigravity CLI & ACP Stdio Server Guide](docs/antigravity_cli.md)*

### 5. 🖥️ Antigravity IDE (Zero-Patch & SQLite Sync)
Route Antigravity IDE through `agy-proxy` without modifying binaries or sync pool tokens directly to IDE SQLite DB:
```bash
# Zero-Patch mode (routes via settings.json without binary modifications):
agy-proxy patch ide-settings --apply

# Switch account and sync directly into IDE SQLite DB (state.vscdb):
agy-proxy switch "user@example.com" --sync-ide-db

# Check current patch status:
agy-proxy patch status

# Binary patch with live health watchdog (optional fallback):
agy-proxy patch install -y
```
👉 *Detailed guide: [Antigravity IDE Integration Guide](docs/antigravity_ide.md)*

---

## 📚 Complete Documentation Index

All in-depth technical guides, configuration references, and tutorials are organized inside the [`docs/`](docs/) directory:

| Section | Topic | Description |
| :--- | :--- | :--- |
| 💻 | [Antigravity CLI & ACP Guide](docs/antigravity_cli.md) | Official `agy` v1.5.5 runner, ACP stdio server for Zed & Neovim, autonomous execution. |
| 🖥️ | [Antigravity IDE Integration](docs/antigravity_ide.md) | Zero-Patch mode, SQLite Protobuf state sync, Dual OAuth, and binary patcher. |
| 🚀 | [Web Mode & Patched Hub](docs/web_mode.md) | No-Electron optimization, unlocked Web Terminal, File Explorer, Battle Mode, v2.33.0 dynamic models. |
| 🌐 | [Web Providers (Gemini Web, Spark, DeepSeek, ChatGPT)](docs/web_providers.md) | Single-chat session reuse, Spark Task agent mode, DSML/`<tool_call>` streaming, IDE prompt compaction. |
| 🤖 | [Claude Code CLI Guide](docs/claude_code.md) | 1-click runner, direct vanilla mode, binary patcher, selective MCP, and debloater. |
| ⌨️ | [OpenAI Codex CLI Guide](docs/codex_cli.md) | Interactive model launcher and `~/.codex/config.toml` setup. |
| 💻 | [IDEs & Coding Agents](docs/ides_and_agents.md) | Cursor, Windsurf, VS Code (Continue, Roo Code, Cline), Aider, and OpenCode. |
| 👥 | [Multi-Account Pool & Failover](docs/accounts_and_failover.md) | OAuth PKCE login, API keys, Hydrogen consumer support, quota balancing, session switching, and proxy tunneling. |
| 🗜 | [Context Compactor (Token Saver)](docs/compactor.md) | Automated long-context summarization and Google 1M token limit prevention. |
| 🧪 | [Experimental Flags & Prompt Sanitizer](docs/experiments_and_flags.md) | v2.33.0 curated flags, live experiment IDs, toxic blocklist, and default prompt restriction stripping. |
| ☁️ | [Cloudflare Edge & Remote Access](docs/cloudflare_and_remote.md) | Serverless 24/7 v2.33.0 edge worker, regional Geo-Bypass, and `cloudflared` HTTPS tunnel. |
| ⚙️ | [Deployment & System Services](docs/deployment_and_services.md) | Docker, Docker Compose, Linux systemd daemon, macOS launchd, standalone binaries, and Android Termux. |
| 💻 | [SDKs, REST API & Env Reference](docs/sdk_and_api.md) | Python OpenAI / Anthropic / Google GenAI SDK examples and environment variable reference. |

---

## 🤖 Supported Models & Aliases

Configure your clients using any of these supported model identifiers:

| Model ID | Provider Family | Context / Output | Best For |
| :--- | :--- | :--- | :--- |
| `gemini-4-neon` / `-low` / `-medium` / `-high` / `-xhigh` (`neon`) | Gemini 4 Neon | 1,048,576 / 262,144 | Next-gen v2.33.0 CloudCode flagship reasoning model (`M532`–`M535`). |
| `gemini-pro-orchestrator-neon` / `orchestrator-neon` | Gemini 4 Neon | 1,048,576 / 262,144 | Multi-step Neon orchestrator preset (`M537`). |
| `gemini-4-hydrogen` / `-low` / `-medium` / `-high` / `-xhigh` (`hydrogen`) | Gemini 4 Hydrogen | 1,048,576 / 262,144 | Core flagship model enabled across all consumer & enterprise OAuth accounts. |
| `gemini-4-carbon` / `carbon` | Gemini 4 Carbon | 1,048,576 / 65,536 | Next-gen CloudCode & Web model with deep reasoning. |
| `gemini-4-argon` / `gemini-4-argon-high` | Gemini 4 Argon | 1,048,576 / 65,536 | Next-gen flagship reasoning model (auto-unlocked). |
| `gemini-4-neon-web-spark` / `spark-neon` *(also `spark-carbon`, `spark-hydrogen`, `spark-auto`)* | Gemini Web Spark | Web Session | Autonomous Gemini Web **Task / Spark** agent mode with multi-turn chat reuse. |
| `gemini-3.8-flash-high` | Gemini 3.8 | 1,048,576 / 65,536 | Ultra-fast coding, agentic workflows, low latency. |
| `gemini-3.8-flash-deepagent` | Gemini 3.8 | 1,048,576 / 65,536 | Autonomous multi-agent coding preset with high reasoning effort. |
| `gemini-3.1-pro-high` | Gemini 3.1 | 1,048,576 / 65,536 | Large refactors, high-precision architecture design. |
| `claude-sonnet-5-5-high` / `claude-opus-5-5-high` | Claude 5.5 | 1,000,000 / 64,000 | Next-gen Claude models with adaptive reasoning & automatic 4.6 fallback. |
| `claude-sonnet-4-6` / `claude-sonnet-4-6-deepagent` | Claude 4.6 | 200,000 / 64,000 | Top-tier code synthesis, Cursor & Claude Code (64k thinking in DeepAgent). |
| `claude-opus-4-6-thinking` / `claude-opus-4-6-deepagent` | Claude 4.6 | 200,000 / 64,000 | Deep extended thinking, mathematically rigorous proofs & autonomous agents. |
| `deepseek-chat` / `deepseek-reasoner` | DeepSeek Web | 65,536 / 8,192 | Free web-backed DeepSeek V3/R1 with session reuse, overflow rotation & DSML tools. |
| `gemini-2.0-flash-lite` / `gpt-4o` *(Web Guest / Alias)* | Web Guest / OpenAI | Reserve / Mapped | Zero-auth reserve web providers (`<tool_call>` supported) & OpenAI aliases. |
| `gemini-3.1-flash-image` | Gemini 3.1 | 65,536 tokens | Image generation & multi-image edits (`/v1/images/*`). |

---

## ⌨️ Common CLI Cheat Sheet

```bash
# Start proxy server on default port 8000:
agy-proxy start

# Start Antigravity in browser Web mode:
agy-proxy web

# Add a Google account via browser OAuth:
agy-proxy login

# List pooled accounts and current quota levels:
agy-proxy accounts

# Switch active Antigravity session to best available account:
agy-proxy switch --best

# List and toggle experimental features:
agy-proxy flags

# Self-update to latest release:
agy-proxy update
```

---

## ⚠️ Disclaimer & Terms of Service Notice

This project is an independent open-source tool developed for developer productivity, resource optimization, and educational research. It is **not** affiliated with, endorsed by, or sponsored by Google LLC or Anthropic PBC. 

Users are solely responsible for ensuring their usage complies with Google and Anthropic applicable Terms of Service and API usage guidelines.

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for full details.
