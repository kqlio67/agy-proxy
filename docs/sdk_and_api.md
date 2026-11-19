# 💻 SDKs, REST API & Environment Reference

Integrate `agy-proxy` directly into your Python scripts, applications, and pipelines using standard AI SDKs.

---

## 🐍 Python SDK Integration Examples

### Official Google GenAI SDK
```python
from google import genai
from google.genai import types

# Route through local agy-proxy
client = genai.Client(
    api_key="dummy",
    http_options=types.HttpOptions(
        base_url="http://127.0.0.1:8000"
    )
)

response = client.models.generate_content(
    model="gemini-3.8-flash-high",
    contents="Explain Quantum Computing in 2 sentences."
)
print(response.text)
```

### Official OpenAI SDK
```python
from openai import OpenAI

# Standard OpenAI client configured with agy-proxy
client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="dummy"
)

response = client.chat.completions.create(
    model="gemini-3.8-flash-high",
    messages=[
        {"role": "system", "content": "You are a helpful coding assistant."},
        {"role": "user", "content": "Write a Python quicksort function."}
    ],
    temperature=0.7
)
print(response.choices[0].message.content)
```

### Official Anthropic SDK
```python
from anthropic import Anthropic

# Standard Anthropic client configured with agy-proxy
client = Anthropic(
    base_url="http://127.0.0.1:8000",
    api_key="dummy"
)

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "Hello Anthropic through Antigravity!"}
    ]
)
print(response.content[0].text)
```

### Image Generation & Multi-Image Editing
```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="dummy")

# Text-to-Image Generation (with dynamic aspect ratio support: 1:1, 16:9, 4:3, 21:9):
img_res = client.images.generate(
    model="gemini-3.1-flash-image",
    prompt="A majestic lion on a savanna sunset 16:9",
    n=1,
)
print("Generated image URL / B64:", img_res.data[0].url)

# Multi-Image Editing (/v1/images/edits):
edit_res = client.images.edit(
    model="gemini-3.1-flash-image",
    image=open("character.png", "rb"),
    prompt="Place this character inside a cybernetic laboratory background",
)
```

---

## 📋 Environment Variables Reference

| Variable | Default | Description |
| :--- | :--- | :--- |
| `AGY_PORT` / `PORT` | `8000` | Port for the proxy HTTP/REST server to listen on. |
| `AGY_HOST` / `HOST` | `127.0.0.1` | Network interface host to bind to (`0.0.0.0` for all interfaces). |
| `AGY_CONFIG_DIR` | `~/.config/agy-proxy` | Directory where configuration, account tokens, and caches are persisted. |
| `AGY_UPSTREAM_PROXY` | *(None)* | Upstream proxy URL (HTTP/HTTPS/SOCKS5) for all outgoing requests. |
| `ALL_PROXY` | *(None)* | Fallback proxy URL for network tunneling. |
| `AGY_PROXY_STRIP_PROMPT_RESTRICTIONS` | `1` *(enabled)* | Strips artificial prompt restrictions (CAPTCHA/bot checks, reverse-engineering bans, guardrail bloat) across CloudCode & Web providers. Set `0`, `false`, `off`, or `disable` to opt out. |
| `AGY_USER_AGENT` | `antigravity/hub/2.33.0 ...` | Overrides the upstream Antigravity client `User-Agent` header. |
| `AGY_WEB_CWD` / `AGY_CALLER_CWD` | *(Current directory)* | Preserves workspace working directory when launching Antigravity Web Mode (`agy-proxy web` / `start_proxy.sh --web`). |
| `ANTIGRAVITY_LS_PATH` | *(Auto)* | Explicit path to `language_server` binary. |
| `ANTIGRAVITY_EDITOR_PATH` | *(Auto)* | Path to Antigravity editor root directory. |
| `CLOUD_CODE_URL` | `http://127.0.0.1:8000` | Redirects official `agy` CLI to route via `agy-proxy`. |
| `AGY_ACCOUNT` | *(None)* | Selects active account for `agy` CLI sessions from proxy pool (by email, name, or account ID). |
| `AGY_CLI_AUTO_COMPACT_OVERRIDE` | *(Auto)* | Controls `agy` CLI internal context auto-compaction (`1` or `0`). |
| `ANTIGRAVITY_CLI_TELEMETRY` | `0` | Disables telemetry reporting in Antigravity CLI and ACP server. |

---

## 🔒 Configuration & Data Security

All sensitive credentials and session files are stored strictly locally:
- **Location**: `~/.config/agy-proxy/`
  - `accounts.json`: Encrypted/persisted Google OAuth tokens & API keys.
  - `config.json`: Custom user configurations and proxy URLs.
  - `logs/`: Diagnostic server logs.
- **File Permissions**: Automatically restricted to user-only read/write (`chmod 600` / `0600`).
- **Telemetry**: Zero external tracking or data transmission. Requests only leave your machine to communicate with the official Google endpoints or your designated upstream proxy.

---

[← Back to Main README](../README.md)
