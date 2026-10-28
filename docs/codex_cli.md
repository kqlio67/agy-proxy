# ⌨️ OpenAI Codex CLI Integration Guide

Integrate OpenAI's **Codex CLI (v0.150+)** directly with `agy-proxy` to leverage high-speed Antigravity models.

---

## 🚀 Integration Methods

### Option A — 1-Click Launcher (`run_codex.sh` / `run_codex.bat`)

Use the built-in helper script to launch Codex CLI with interactive model selection:

#### Linux / macOS:
```bash
# Interactive mode with Antigravity model catalog:
./run_codex.sh

# Launch directly with a specific model:
./run_codex.sh -m gemini-3.8-flash-high
./run_codex.sh -m claude-sonnet-4-6
./run_codex.sh -m claude-sonnet-4-6-deepagent
```

#### Windows:
```cmd
:: Interactive mode or specific model:
run_codex.bat
run_codex.bat -m claude-sonnet-4-6
run_codex.bat -m gemini-3.8-flash-high
```

---

### Option B — Ephemeral Run (`agy-proxy codex`)

Run Codex through the proxy on the fly without modifying your personal configuration files:

```bash
# Launches codex through the proxy without modifying ~/.codex/config.toml:
agy-proxy codex --port 8000 --model gemini-3.8-flash-high
```

---

### Option C — Persistent Configuration (`~/.codex/config.toml`)

Automatically update your global Codex settings:

```bash
# Configure ~/.codex/config.toml with native Antigravity model provider and catalog:
agy-proxy setup-codex --port 8000 --model gemini-3.8-flash-high

# Or restore original config at any time:
agy-proxy restore-codex
```

### Manual Configuration Example
If editing `~/.codex/config.toml` manually (recommended for Codex v0.150+):

```toml
model_provider = "antigravity"
model = "gemini-3.8-flash-high"
model_catalog_json = "~/.codex/antigravity_models.json"

[model_providers.antigravity]
name = "Antigravity Proxy"
base_url = "http://127.0.0.1:8000/v1"
wire_api = "responses"
model_catalog_url = "http://127.0.0.1:8000/api/codex/models.json"
```

> **Note on Compatibility:**
> `agy-proxy setup-codex` automatically configures both the modern `[model_providers.antigravity]` section (for Codex v0.150+) and the fallback `openai_base_url` key, ensuring seamless compatibility across all Codex CLI versions.

---

[← Back to Main README](../README.md)
