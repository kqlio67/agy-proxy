# ⚡ Antigravity Proxy (`agy-proxy`)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](docs/deployment_and_services.md#docker--docker-compose)
[![Termux](https://img.shields.io/badge/Android-Termux-green.svg)](docs/deployment_and_services.md#android-via-termux)
[![Cloudflare Workers](https://img.shields.io/badge/Cloudflare-Workers-orange.svg)](docs/cloudflare_and_remote.md)

**Antigravity Proxy** (`agy-proxy`) is a high-performance local AI gateway that supercharges the official **Google Antigravity 2.0** ecosystem.

It unlocks high-tier models (**Gemini 3.8 Flash**, **Gemini 3.1 Pro**, **Claude Sonnet 4.6**, **Claude Opus 4.6 Thinking**) through standard **OpenAI**, **Anthropic**, and **Google GenAI** API endpoints with multi-account pooling, automated failover, token-saving context compaction, and zero-Electron RAM optimization.

---

## 🌟 Why agy-proxy?

- 🆓 **Zero-Cost High-Tier AI**: Access Gemini 3.8 and Claude models through Antigravity without paid API subscriptions.
- 🔄 **Universal API Compatibility**: Drop-in compatible with standard OpenAI (`/v1/chat/completions`), Anthropic (`/v1/messages`), and Gemini endpoints.
- 👥 **Multi-Account Pool & Auto-Failover**: Pool multiple Google accounts and API keys; if one hits rate limits, traffic rotates seamlessly without crashing your IDE.
- 🚀 **Antigravity Web Hub (Saves RAM)**: Run the full Antigravity 2.0 interface directly in your browser without the heavy Electron wrapper.
- 🗜 **Context Compactor (Token Saver)**: Auto-compresses long chat histories using fast flash-lite models, preventing Google's 1M token limit and saving 80%–95% tokens.
- 🖥️ **1-Click IDE & CLI Integrations**: Native launch scripts and configurations for Antigravity IDE, Claude Code CLI, Cursor, Windsurf, VS Code (Continue, Roo Code, Cline), Codex CLI, and Aider.

---

## ⚡ Quick Start (Under 1 Minute)

### 1. One-Line Fast Install

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

### 3. Open the Web Dashboard

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

### 2. 🤖 Claude Code CLI
Run Anthropic's official Claude Code CLI routed through `agy-proxy`:
```bash
# Launch interactive session:
agy-proxy claude

# Launch with specific model:
agy-proxy claude -m claude-sonnet-4-6
```
👉 *Detailed guide: [Claude Code CLI Integration](docs/claude_code.md)*

### 3. ⌨️ OpenAI Codex CLI
Run OpenAI Codex CLI with full Antigravity model catalog:
```bash
# Launch interactive session:
agy-proxy codex

# Launch with specific model:
agy-proxy codex -m claude-sonnet-4-6
```
👉 *Detailed guide: [OpenAI Codex CLI Integration](docs/codex_cli.md)*

### 4. 🖥️ Antigravity IDE Patcher
Route Antigravity IDE's internal `language_server` through `agy-proxy`:
```bash
# Check current patch status:
agy-proxy patch status

# Patch automatically with live health watchdog:
agy-proxy patch install -y

# Restore original binary at any time:
agy-proxy patch restore -y
```
👉 *Detailed guide: [Antigravity IDE Integration Guide](docs/antigravity_ide.md)*

---

## 📚 Complete Documentation Index

All in-depth technical guides, configuration references, and tutorials are organized inside the [`docs/`](docs/) directory:

| Section | Topic | Description |
| :--- | :--- | :--- |
| 🖥️ | [Antigravity IDE Integration](docs/antigravity_ide.md) | `language_server` patcher, automatic discovery, live watchdog, and flag table. |
| 🚀 | [Web Mode & Patched Hub](docs/web_mode.md) | No-Electron optimization, unlocked Web Terminal, File Explorer, Battle Mode. |
| 🤖 | [Claude Code CLI Guide](docs/claude_code.md) | 1-click runner, persistent `~/.claude/settings.json`, and Error 500 role fix. |
| ⌨️ | [OpenAI Codex CLI Guide](docs/codex_cli.md) | Interactive model launcher and `~/.codex/config.toml` setup. |
| 💻 | [IDEs & Coding Agents](docs/ides_and_agents.md) | Cursor, Windsurf, VS Code (Continue, Roo Code, Cline), Aider, and OpenCode. |
| 👥 | [Multi-Account Pool & Failover](docs/accounts_and_failover.md) | OAuth PKCE login, API keys, quota balancing, session switching, and proxy tunneling. |
| 🗜 | [Context Compactor (Token Saver)](docs/compactor.md) | Automated long-context summarization and Google 1M token limit prevention. |
| 🧪 | [Experimental Flags Manager](docs/experiments_and_flags.md) | Curated developer feature toggles and protection against broken internal flags. |
| 🌐 | [Cloudflare Edge & Remote Access](docs/cloudflare_and_remote.md) | Serverless 24/7 edge worker, regional Geo-Bypass, and `cloudflared` HTTPS tunnel. |
| ⚙️ | [Deployment & System Services](docs/deployment_and_services.md) | Docker, Docker Compose, Linux systemd daemon, macOS launchd, and Android Termux. |
| 💻 | [SDKs, REST API & Env Reference](docs/sdk_and_api.md) | Python OpenAI / Anthropic / Google GenAI SDK examples and configuration reference. |

---

## 🤖 Supported Models & Aliases

Configure your clients using any of these supported model identifiers:

| Model ID | Provider Family | Context Window | Best For |
| :--- | :--- | :--- | :--- |
| `gemini-3.8-flash-high` | Gemini 3.8 | 1,048,576 tokens | Ultra-fast coding, agentic workflows, low latency. |
| `gemini-3.8-flash-deepagent` | Gemini 3.8 | 1,048,576 tokens | Autonomous multi-agent coding preset with high reasoning effort. |
| `gemini-4-argon` / `gemini-4-argon-high` | Gemini 4 Argon | 1,048,576 tokens | Next-gen flagship reasoning model (auto-unlocked). |
| `gemini-3.1-pro-high` | Gemini 3.1 | 1,048,576 tokens | Large refactors, high-precision architecture design. |
| `claude-sonnet-4-6` | Claude 3.5/3.7/4.6 | 200,000 tokens | Top-tier code synthesis, Cursor & Claude Code. |
| `claude-sonnet-4-6-deepagent` | Claude 4.6 | 200,000 tokens | Autonomous agent preset with 64k extended thinking budget. |
| `claude-opus-4-6-thinking` | Claude 4.6 | 200,000 tokens | Deep extended thinking, mathematically rigorous proofs. |
| `claude-opus-4-6-deepagent` | Claude 4.6 | 200,000 tokens | Maximum capability autonomous agent with 64k thinking budget. |
| `gemini-3.1-flash-image` | Gemini 3.1 | 65,536 tokens | Image generation & multi-image edits (`/v1/images/*`). |
| `gpt-4o` *(alias)* | OpenAI Mapping | Maps to Flash High | Drop-in replacement for OpenAI-default tools. |

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
