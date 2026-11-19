# 💻 IDEs & Coding Agents Integration Guide

Configure popular AI code editors and CLI agents to route through `agy-proxy` using its OpenAI, Anthropic, and Gemini compatible endpoints.

---

## 🖱️ Cursor

1. Open **Cursor Settings** -> **Models**.
2. Enable **OpenAI API Key** and enter any dummy text (e.g. `dummy` or `sk-antigravity`).
3. Click **Override OpenAI Base URL** and set:
   ```
   http://127.0.0.1:8000/v1
   ```
4. In the model selector, add custom model names supported by `agy-proxy`:
   - `gemini-4-neon-high` / `gemini-4-hydrogen-high`
   - `gemini-3.8-flash-high`
   - `gemini-3.1-pro-high`
   - `claude-sonnet-4-6` / `claude-sonnet-5-5-high`
   - `deepseek-chat` / `deepseek-reasoner` *(via DeepSeek Web with DSML tool calling)*

---

## 🌊 Windsurf / Codeium

1. Open **Windsurf Settings** -> **AI Provider**.
2. Select **OpenAI Compatible Endpoint**.
3. Set **Base URL**:
   ```
   http://127.0.0.1:8000/v1
   ```
4. Set **API Key**: `dummy`.
5. Enter model name: `gemini-3.8-flash-high`.

---

## 🧩 VS Code — Continue Extension

Add `agy-proxy` to your `~/.continue/config.yaml` or `~/.continue/config.json`:

```yaml
models:
  - name: "Gemini 3.8 Flash (Antigravity)"
    provider: "openai"
    model: "gemini-3.8-flash-high"
    apiBase: "http://127.0.0.1:8000/v1"
    apiKey: "dummy"
  - name: "Claude Sonnet 4.6 (Antigravity)"
    provider: "anthropic"
    model: "claude-sonnet-4-6"
    apiBase: "http://127.0.0.1:8000"
    apiKey: "dummy"
```

---

## 🤖 VS Code — Roo Code / Cline (Claude Dev)

Roo Code and Cline require robust tool calling and strict schema compatibility. `agy-proxy` has dedicated native translation layers for both.

### Provider Configuration
- **API Provider**: `OpenAI Compatible` or `Anthropic`
- **Base URL**: `http://127.0.0.1:8000/v1` (for OpenAI) or `http://127.0.0.1:8000` (for Anthropic)
- **API Key**: `dummy`
- **Model ID**: `gemini-3.8-flash-high` or `claude-sonnet-4-6`

### 🛡️ Built-in Roo Code / Cline Optimizations
- **Custom Tool Schema Unpacking**: Automatically unpacks complex tool definitions from `tool.custom.input_schema`, `tool.custom.parameters`, `tool.custom.name`, and `tool.custom.description`.
- **Schema Sanitization (`sanitize_gemini_schema`)**: Flattens `anyOf`, `oneOf`, `allOf`, maps union nullables (`["string", "null"]` -> `type: "string", nullable: true`), and strips unsupported metadata (`$schema`, `definitions`, `additionalProperties`) preventing upstream Error 400 validation failures.
- **Tool Pairing Integrity (`ensure_tool_pairing_integrity`)**: Enforces strict 1:1 pairing between model `functionCall` turns and user `functionResponse` turns, caching thought signatures and resolving orphaned responses so conversation histories never crash downstream translations.
- **Web Provider IDE Prompt Compaction & Tool Calling**: Even when routing Roo Code / Cline through free Web Providers (`deepseek-chat`, `deepseek-reasoner`, `gemini-2.0-flash-lite`, `gpt-4o`), `agy-proxy` automatically compacts massive IDE system prompts, strips artificial restrictions, handles `role: "developer"`, and parses streaming `DSML` / `<tool_call>` blocks back into standard tool calls.

---

## ⚡ Zed & Neovim via Agent Client Protocol (ACP)

You can connect **Zed**, **Neovim** (`avante.nvim` / `codecompanion.nvim`), and other ACP-compatible clients to Google Antigravity as an external agent via `agy-proxy acp`:

```bash
# Start ACP stdio server hooked to proxy:
agy-proxy acp --danger
```

### Zed Configuration (`~/.config/zed/settings.json`)
```json
{
  "agent": {
    "provider": "acp",
    "command": "agy-proxy",
    "args": ["acp", "--danger"]
  }
}
```

👉 *Detailed guide: [Google Antigravity CLI & ACP Guide](antigravity_cli.md)*

---

## 🧑‍💻 CLI Coding Agents (Aider, OpenCode)

### Aider

```bash
# Using OpenAI compatible endpoint:
OPENAI_API_BASE="http://127.0.0.1:8000/v1" \
OPENAI_API_KEY="dummy" \
aider --model openai/gemini-3.8-flash-high

# Using Anthropic compatible endpoint:
ANTHROPIC_BASE_URL="http://127.0.0.1:8000" \
ANTHROPIC_API_KEY="dummy" \
aider --model anthropic/claude-sonnet-4-6
```

---

[← Back to Main README](../README.md)
