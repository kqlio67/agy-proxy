# ⚡ Antigravity Proxy (agy-proxy)

A high-performance Python proxy server that exposes Google Antigravity & Gemini Code Assist as **standard OpenAI (`/v1/chat/completions`)**, **Anthropic Claude (`/v1/messages`)**, and **Gemini Native (`/v1beta/...`)** API endpoints.

Equipped with **Multi-Account Pooling**, **Automatic 429 Quota Failover**, and a **built-in Web Dashboard & Live Playground**.

---

## ⚠️ Disclaimer & Terms of Service Notice

> [!IMPORTANT]
> **This project is developed independently for educational, research, API interoperability, and personal non-commercial evaluation purposes only.**
>
> - **No Affiliation**: This software is not affiliated with, endorsed by, maintained by, or sponsored by Google LLC, Anthropic PBC, or any of their subsidiaries.
> - **User Responsibility**: By using this software, you acknowledge and agree that you are solely responsible for your own account usage and for complying with the respective platform Terms of Service, Acceptable Use Policies, rate limits, and API licensing agreements of Google Cloud, Google AI Studio, Anthropic, and any other third-party services.
> - **No Warranty**: This software is provided "AS IS" without warranties or conditions of any kind, either express or implied. The authors and maintainers assume no liability for any direct, indirect, incidental, or consequential damages resulting from the use or misuse of this software, including but not limited to quota exhaustion, service disruption, or account suspension.

---

## 🌟 Key Features

- 🔄 **OpenAI & Anthropic & Gemini Compatible APIs**:
  - Full support for streaming (`stream: true` SSE) and non-streaming responses.
  - Thinking / Reasoning process extracted to `delta.reasoning_content` (OpenAI) and `thinking` blocks (Anthropic) for reasoning models (`gemini-3.7-flash-high`, `claude-opus-4-6-thinking`).
  - Native Multi-turn Tool & Function Calling support (`tools`, `tool_choice`, `tool_use`, `tool_result`).
  - Multimodal input support (Images via base64 data URIs and URLs).
  - 🌐 **Built-in Live Web Search**: Native **Google Grounding Search (Vertex AI)** with automatic multi-engine fallbacks (**DuckDuckGo**, **Bing**, **Brave**) for Claude Code (`WebSearch`) without requiring external API keys.
- 🗜 **Context Compactor & Auto-Summarization (Token Saver)**:
  - Automatically compresses long conversation histories using `gemini-3.1-flash-lite` before reaching token limits, saving **80% to 95%+ tokens** and preventing Google's 1,048,576 token hard limit (Error 400).
  - Native interception of Claude Code `/compact` and `/autocompact` commands with official 9-section summarization schema.
  - Interactive threshold slider (30k – 200k tokens) and toggle controls in the Web Dashboard.
- ⚡ **Intelligent Background Task Optimization**:
  - Automatically detects non-interactive tasks (title generation, context compaction, checkpoints) and routes them to lightweight, fast models (`gemini-3.1-flash-lite`) with 0 thinking budget to save 100% of your primary model quotas.
- 👥 **Multi-Account Pooling & Limit Bypass**:
  - Pool multiple Google Antigravity accounts simultaneously to multiply your rate limits and concurrent request capacity.
  - **Automatic 429 Failover**: When Account A exhausts its quota bucket, the proxy seamlessly retries and routes the request to Account B without dropping the session!
  - **Zero-Delay Recovery**: Injects standard `Retry-After: 2` headers to prevent Claude Code from locking up in 4-minute exponential backoff retry delays.
- 🔑 **Interactive OAuth PKCE Login (Web UI & CLI)**:
  - Add secondary Google accounts in 2 clicks via the Web UI ("➕ Add Google Account") or via terminal: `uv run python main.py auth login`.
  - Support for Google AI Studio API Keys with automatic dynamic model discovery.
- 📊 **Modern Web UI Dashboard & Playground**:
  - View all pooled Google accounts, avatars, active tiers, and live Gemini & Claude quota progress bars.
  - Interactive live chat playground with markdown rendering and collapsible thinking blocks.
  - Dynamic Context Compactor card with configurable auto-summarization thresholds.
  - Toggle switch to enable/pause specific accounts and one-click account deletion.
- 🚀 **One-Click Launchers**:
  - Pre-configured shell scripts: `./run_claude.sh` (with custom port and model flags) and `./start_proxy.sh`.

---

## 🚀 Quick Start

### 1. One-Line Fast Install (Linux, macOS, Windows) ⚡

Install the pre-built standalone binary directly with a single command without needing Python:

#### 🔹 Linux & macOS
```bash
curl -fsSL https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.sh | bash
```

#### 🔹 Windows (PowerShell)
```powershell
irm https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.ps1 | iex
```

#### 🔹 Windows (Command Prompt)
```cmd
curl -fsSL https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.cmd -o install.cmd && install.cmd && del install.cmd
```

Then simply start the proxy anywhere:
```bash
agy-proxy --port 8000
```

---

### 2. Run with Docker / Docker Compose 🐳

```bash
# Quick Docker Run:
docker run -d -p 8000:8000 \
  -v ~/.config/agy-proxy:/home/appuser/.config/agy-proxy \
  --name agy-proxy ghcr.io/kqlio67/agy-proxy:latest

# Or using Docker Compose:
docker compose up -d
```

---

### 3. Run via 1-Click Scripts (Repository Source)

- **Linux & macOS**:
  ```bash
  ./start_proxy.sh --port 8000
  ```
- **Windows (CMD / PowerShell / Explorer)**:
  ```cmd
  start_proxy.bat --port 8000
  ```

---

### 4. Run via Python / UV / Pip (Source)

```bash
# Clone the repository
git clone https://github.com/kqlio67/agy-proxy.git
cd agy-proxy

# Run instantly with UV (Recommended):
uv run python main.py --port 8000

# OR install with standard pip:
pip install -e .
agy-proxy --port 8000

# OR run directly with Python:
python main.py --port 8000
```

---

### 5. In-Place Self Update 🔄

Check and update Antigravity Proxy directly from the CLI:

```bash
# Check if a new version is available:
agy-proxy update --check

# Download & apply the latest update:
agy-proxy update
```

---

### 6. Run on Android via Termux 📱

Run Antigravity Proxy directly on your Android phone without root:

```bash
# 1. Update Termux and install Python + Git
pkg update -y && pkg install python git -y

# 2. Clone and install dependencies
git clone https://github.com/kqlio67/agy-proxy.git
cd agy-proxy
pip install -r requirements.txt

# 3. Start the proxy
python main.py --port 8000

# (Optional: expose to your local Wi-Fi network so your PC/laptop can connect to phone)
python main.py --host 0.0.0.0 --port 8000
```
Open **`http://localhost:8000`** in Chrome / Firefox on your phone to view the dashboard!

---

### 7. Run via Pre-built Standalone Binaries (Zero Dependencies)

Download the single executable for your OS & architecture from **[GitHub Releases](https://github.com/kqlio67/agy-proxy/releases)**:

- **Linux (x86_64 / amd64)**: `./agy-proxy --port 8000`
- **Linux ARM64 (Raspberry Pi 3/4/5, Orange Pi, ARM VPS)**: `./agy-proxy --port 8000`
- **Windows (x64)**: `agy-proxy.exe --port 8000`
- **macOS (Apple Silicon M1/M2/M3/M4)**: `./agy-proxy --port 8000`

*(To compile a single executable locally on your machine: `python build_binary.py`)*

---

Open **`http://localhost:8000`** in your browser to access the Web Dashboard & Account Pool Manager!

---

## 🛠 Integration Guides

### 1. 🤖 Claude Code CLI

#### 🔹 Linux / macOS / Termux
```bash
# Default launch (port 8000, model gemini-3.7-flash-high)
./run_claude.sh

# Launch on a custom proxy port:
./run_claude.sh -p 8080

# Launch with a specific model (e.g., Claude Sonnet 4.6):
./run_claude.sh -p 8080 -m claude-sonnet-4-6

# Pass direct arguments to Claude Code:
./run_claude.sh -p 8080 -- --allow-dangerously-skip-permissions "Explain this repo"
```

#### 🔹 Windows (Command Prompt / PowerShell)
```cmd
:: Default launch
run_claude.bat

:: Launch with custom port and model
run_claude.bat -p 8080 -m gemini-3.7-flash-high
```

#### 🔹 Manual Launch (Direct Environment Variables)
```bash
# Linux / macOS / Termux:
ANTHROPIC_BASE_URL="http://127.0.0.1:8000" ANTHROPIC_API_KEY="dummy" ANTHROPIC_MODEL="gemini-3.7-flash-high" claude

# Windows PowerShell:
$env:ANTHROPIC_BASE_URL="http://127.0.0.1:8000"; $env:ANTHROPIC_API_KEY="dummy"; $env:ANTHROPIC_MODEL="gemini-3.7-flash-high"; claude

# Windows CMD:
set ANTHROPIC_BASE_URL=http://127.0.0.1:8000 && set ANTHROPIC_API_KEY=dummy && set ANTHROPIC_MODEL=gemini-3.7-flash-high && claude
```

---

### 2. 💻 IDEs & Code Editors (Cursor, Windsurf, VS Code)

#### 🔹 Cursor
1. Open **Settings** (`Ctrl+,` or `Cmd+,`) -> **Models**.
2. Enable **OpenAI API Key**:
   - **OpenAI Base URL**: `http://127.0.0.1:8000/v1`
   - **OpenAI API Key**: `dummy` (or your `PROXY_API_KEY`)
3. In the **Model Names** section, add:
   - `gemini-3.7-flash-high`
   - `claude-3-7-sonnet`
   - `gpt-4o`

#### 🔹 Windsurf / Codeium
1. Open **Settings** -> **Custom OpenAI Model Provider**.
2. Configure:
   - **Base URL**: `http://127.0.0.1:8000/v1`
   - **API Key**: `dummy`
   - **Model**: `gemini-3.7-flash-high`

#### 🔹 VS Code — Continue Extension
Add the following to your `~/.continue/config.json`:
```json
{
  "models": [
    {
      "title": "Antigravity Gemini 3.7 Flash",
      "provider": "openai",
      "model": "gemini-3.7-flash-high",
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "dummy"
    },
    {
      "title": "Antigravity Claude 3.7 Sonnet",
      "provider": "anthropic",
      "model": "claude-3-7-sonnet",
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "dummy"
    }
  ]
}
```

#### 🔹 VS Code — Roo Code / Cline (Claude Dev)
1. Open Roo Code / Cline settings (`API Provider`).
2. Select provider **Anthropic** or **OpenAI Compatible**:
   - **Base URL**: `http://127.0.0.1:8000` *(for Anthropic)* or `http://127.0.0.1:8000/v1` *(for OpenAI)*
   - **API Key**: `dummy`
   - **Model ID**: `gemini-3.7-flash-high` or `claude-3-7-sonnet`

---

### 3. ⌨️ CLI Coding Tools (Aider, OpenCode)

#### 🔹 Aider
```bash
# Using OpenAI endpoint
OPENAI_API_BASE="http://127.0.0.1:8000/v1" \
OPENAI_API_KEY="dummy" \
aider --model openai/gemini-3.7-flash-high

# Using Anthropic endpoint
ANTHROPIC_BASE_URL="http://127.0.0.1:8000" \
ANTHROPIC_API_KEY="dummy" \
aider --model anthropic/claude-3-7-sonnet
```

---

### 4. 🌐 Cloudflare Edge Worker & Remote Access

The repository includes a ready-to-deploy **Cloudflare Worker** ([`cloudflare/`](cloudflare/)) supporting two modes:

#### 🔹 Mode 1: Geo-Bypass Upstream Gateway (Recommended for local dev)
Routes all Google CloudCode API requests from your local `agy-proxy` through Cloudflare's global edge network to bypass regional IP restrictions with 0% VPN overhead:
```bash
# Deploy worker in 1 command:
cd cloudflare && npx wrangler deploy

# Start local proxy routing through Cloudflare:
agy-proxy --port 8000 --cloudflare-url "https://agy-proxy-edge.<your-subdomain>.workers.dev"

# OR via environment variable:
export CLOUDFLARE_UPSTREAM_URL="https://agy-proxy-edge.<your-subdomain>.workers.dev"
agy-proxy --port 8000
```

#### 🔹 Mode 2: 100% Serverless Edge Mode (No PC needed, 24/7 in Cloud)
Deploy your account pool directly into Cloudflare's serverless edge:
```bash
cd cloudflare
# Upload your local accounts pool to Cloudflare Secrets
npx wrangler secret put ACCOUNTS_JSON < ~/.config/agy-proxy/accounts.json
npx wrangler deploy
```
*Use your Worker URL directly in Cursor / Claude Code / Windsurf:* `https://agy-proxy-edge.<your-subdomain>.workers.dev/v1`

#### 🔹 Quick Instant HTTPS Tunnel (`cloudflared`)
If you just want an instant secure public HTTPS URL for your local proxy without deploying anything:
```bash
cloudflared tunnel --url http://127.0.0.1:8000
# Use generated URL: https://<random>.trycloudflare.com/v1 in your clients
```

👉 *See [`cloudflare/README.md`](cloudflare/README.md) for the complete Cloudflare deployment reference.*

---

## 👥 Multi-Account Pool Management

### Adding Accounts via Web Dashboard
1. Open `http://localhost:8000` in your browser.
2. Click **"➕ Add Google Account"** or **"🔑 Add AI Studio Key"**.
3. Authorize via Google and paste the resulting authorization code/URL or API key.

### Adding Accounts via CLI
```bash
uv run python main.py auth login
```

### Viewing Account Pool & Quotas
```bash
uv run python main.py auth list
```

---

## 🗜 Context Compactor & Auto-Summarization (Token Saver)

Antigravity Proxy features an intelligent **Context Compactor Engine** that continuously monitors conversation length and automatically summarizes older conversation turns when token limits approach threshold.

### 🌟 Why it matters:
- **80% to 98% Token Reduction**: Large 500,000+ token sessions (with full file contents, diffs, and bash outputs) are safely compressed down into a clean ~1,500-token structured context.
- **Zero 400 Context Overflow Errors**: Prevents hitting Google CloudCode's hard limit of `1,048,576 tokens`.
- **Zero Claude Quota Wasted**: Compactions and summaries are processed in milliseconds via `gemini-3.1-flash-lite`, using **0% of your primary model quota**.
- **Full History Retention**: Uses Claude Code's official 9-section summarization prompt schema, retaining all project architectures, file paths, pending tasks, and user instructions.

### ⚙️ Configuration (Web UI & REST API)
You can configure the compactor dynamically from the Web Dashboard (`http://localhost:8000`) or via REST API:

- **Auto-Summarize Threshold**: Set token threshold from `30,000` to `200,000` tokens (Default: `85,000` tokens).
- **Preserve Last Messages**: Choose how many recent messages to keep uncompressed in full detail (e.g., 4 to 8 messages).
- **Manual `/compact` Command**: Type `/compact` inside Claude Code CLI anytime to trigger instantaneous background compression.

```bash
# Get current compactor settings
curl http://127.0.0.1:8000/api/context/settings

# Update threshold to 75,000 tokens
curl -X POST http://127.0.0.1:8000/api/context/settings \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "threshold_tokens": 75000, "keep_last_n": 4}'
```

---

## 🌐 Live Web Search & Google Grounding

Antigravity Proxy natively equips coding agents (like Claude Code) with live internet access without needing paid API keys from third-party search providers.

### 🔍 Search Architecture:
1. **Google Grounding (Vertex AI)**: Real-time search indexing directly from Google's search engine.
2. **Multi-Engine Concurrent Fan-Out**: Automatic parallel querying across **DuckDuckGo**, **Bing**, and **Brave** with URL deduplication, domain ranking, and domain filtering (`site:` filters).
3. **Transparent Claude Tool Interception**: Automatically handles Claude Code's `WebSearch` and `web_search` tool calls, returning clean, grounded results with citations and URLs.

---

## 📋 Supported Models & Aliases

| Client Request (Alias) | Google Antigravity Backend Model | Description |
|---|---|---|
| `gemini-3.7-flash-high`, `gemini-pro`, `gpt-4o` | `gemini-3.7-flash-high` | ⚡ Flagship model with extended Thinking / Reasoning |
| `gemini-3.1-pro-high`, `gemini-3.1-pro` | `gemini-3.1-pro-high` | 🧠 Pro model for deep analysis & complex logic |
| `gemini-3.1-flash-lite`, `gpt-4o-mini` | `gemini-3.1-flash-lite` | 💡 Ultra-fast lightweight model |
| `claude-sonnet-4-6`, `claude-3-7-sonnet`, `claude-3-5-sonnet` | `claude-sonnet-4-6` | 🚀 Native Claude Sonnet via Antigravity backend |
| `claude-opus-4-6-thinking`, `claude-3-opus` | `claude-opus-4-6-thinking` | 🔬 Native Claude Opus with thinking process |
| `gpt-oss-120b-medium`, `gpt-oss-120b` | `gpt-oss-120b-medium` | 🌐 Open Source 120B model |

---

## 💻 SDK Integration Examples

### Python (Official OpenAI SDK)
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="dummy"
)

response = client.chat.completions.create(
    model="gemini-3.7-flash-high",
    messages=[{"role": "user", "content": "Explain how quantum computers work in simple terms."}],
    stream=True
)

for chunk in response:
    if hasattr(chunk.choices[0].delta, "reasoning_content") and chunk.choices[0].delta.reasoning_content:
        print(chunk.choices[0].delta.reasoning_content, end="", flush=True)
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### Python (Official Anthropic SDK)
```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://127.0.0.1:8000",
    api_key="dummy"
)

message = client.messages.create(
    model="claude-3-7-sonnet",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello! Describe your capabilities."}]
)

print(message.content[0].text)
```

---

## 🔒 Configuration & Data Security

- 📁 **Account Pool Configuration**: `~/.config/agy-proxy/accounts.json`
- 🔍 **Zero-Config Session Auto-Discovery**: The proxy automatically discovers existing local tokens from:
  - `~/.gemini/antigravity-cli/antigravity-oauth-token`
  - `~/.gemini/antigravity-ide/antigravity-oauth-token`
- 🛡️ All credentials and tokens are stored **strictly locally** on your machine.

---

## 📄 License

Distributed under the **GNU General Public License v3.0 (GPLv3)**. See [`LICENSE`](LICENSE) for more information.
