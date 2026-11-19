# 🌐 Web Providers (Gemini Web, Spark Agent, DeepSeek Web & ChatGPT Web)

In addition to Google CloudCode OAuth and AI Studio API keys, `agy-proxy` integrates native web-backed providers with unified IDE prompt compaction, automatic prompt restriction stripping, `role: "developer"` support, multi-turn session continuation, and streaming tool calling (`DSML` / `<tool_call>`).

---

## 🌟 Supported Web Providers

| Provider | Preset ID | Models | Auth Mode | Default State | Highlights |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gemini Web (Auth)** | Google Cookies / WebChannel | `gemini-4-neon-web-*`, `gemini-4-carbon-web-*`, `gemini-4-hydrogen-web-*`, `gemini-4-argon-web-*`, `gemini-3.8-flash-web-*`, `gemini-auto-web`, `*-web-spark` | Authenticated Google Web Session | Active when cookies added | Dynamic `otAQ7b` model hash resolution, **Spark / Task** autonomous agent mode, single-chat multi-turn continuation |
| **Gemini Guest** | `gemini-web-guest` | `gemini-2.0-flash-lite`, `gemini-flash-lite` | Zero-Auth (CDP / WebChannel) | Reserve (`enabled: false`) | Single-chat session reuse in CDP & WebChannel, full `<tool_call>` streaming & non-streaming tool calls |
| **DeepSeek Web** | `deepseek-web` | `deepseek-chat`, `deepseek-reasoner` | DeepSeek Web Token | Enabled (`enabled: true`) | Multi-turn session reuse with automatic overflow rotation (`56,000` token threshold), DSML tool calling (`supports_tools: true`) |
| **ChatGPT Web** | `chatgpt-web` / `chatgpt-web-guest` | `gpt-4o`, `gpt-4o-mini`, `o3-mini`, `chatgpt-4o-latest` | Zero-Auth Guest or Auth Token | Reserve (`enabled: false`) | Zero-auth guest pipeline with vision & web search, unified prompt compaction and tool calling |

> [!NOTE]
> **Reserve-by-Default Guest Policy**: Starting in `v2.3.0`, zero-auth guest presets (`gemini-web-guest` and `chatgpt-web-guest`) default to `enabled: false` so your primary OAuth and API key pool handles traffic first. Enable them in the Web Dashboard (`http://127.0.0.1:8000`) whenever you want zero-auth reserve fallbacks.

---

## ✨ Gemini Web, Dynamic Models & Spark Task Mode

Through reverse-engineering of live Gemini Web traffic (`otAQ7b` model catalog and `hNvQHb` RPC payloads), `agy-proxy` provides native support for the full Gemini 4 Web lineup:

### 1. Dynamic Web Models & Aliases
- **Gemini 4 Neon (Web)**: `gemini-4-neon-web` (`-low`, `-medium`, `-high`, `-xhigh`)
- **Gemini 4 Carbon (Web)**: `gemini-4-carbon-web` (`-low`, `-medium`, `-high`)
- **Gemini 4 Hydrogen (Web)**: `gemini-4-hydrogen-web` (`-low`, `-medium`, `-high`, `-xhigh`)
- **Gemini Auto (Web)**: `gemini-auto-web` (alias: `gemini-auto`)
- **Gemini 3.8 Flash / 3.1 Pro / 3.5 Flash-Lite (Web)**: `gemini-3.8-flash-web-*`, `gemini-3.1-pro-web-*`, `gemini-3.5-flash-lite-web-*`

### 2. Autonomous Spark / Task Agent Mode
Trigger Gemini Web's built-in **Task / Spark** agent mode (segmented `['Chat', 'Task']` mode with thinking trace extraction) using any Spark model ID or shorthand alias:
- `gemini-4-neon-web-spark` *(aliases: `gemini-4-neon-spark`, `gemini-4-neon-task`, `spark-neon`)*
- `gemini-4-carbon-web-spark` *(aliases: `gemini-4-carbon-spark`, `gemini-4-carbon-task`, `spark-carbon`)*
- `gemini-4-hydrogen-web-spark` *(aliases: `gemini-4-hydrogen-spark`, `gemini-4-hydrogen-task`, `spark-hydrogen`)*
- `gemini-auto-web-spark` *(aliases: `gemini-auto-spark`, `gemini-auto-web-task`, `spark-auto`)*
- `gemini-4-argon-web-spark`, `gemini-3.8-flash-web-spark`

### 3. Single-Chat Multi-Turn Continuation
Instead of creating a brand-new conversation tab on every turn:
- **Gemini Web & WebChannel** preserve `client_uuid` in `x-goog-ext-525005358-bin` and track conversation pointers (`c_<id>` / `r_<id>`).
- **Gemini Guest (CDP & WebChannel)** caches active sessions and reuses the existing conversation thread across turns, sending only the incremental continuation prompt.

---

## 🐋 DeepSeek Web Session Reuse & Overflow Rotation

`DeepSeekWebProvider` (`deepseek-chat` and `deepseek-reasoner`) automatically tracks multi-turn conversations across OpenAI/Anthropic client requests:
- **Zero Chat Spam**: Reuses the same upstream DeepSeek chat session across consecutive turns and appends only the latest delta turn (`format_web_continuation_prompt`).
- **Automatic Overflow Rotation**: Tracks accumulated session tokens and transparently rotates to a fresh chat session once a session reaches `_MAX_SESSION_TOKENS` (`56,000` tokens), keeping up to `200` active sessions cached.
- **Full Tool Calling**: Emits and parses structured DSML tool calls (`supports_tools: true`) so coding agents (Cursor, Roo Code, Cline, Claude Code, Codex CLI) work seamlessly over DeepSeek Web.

---

## 🧩 Unified Web Prompt & Tool-Call Pipeline (`web_prompt_utils.py`)

All web providers share a single unified pipeline in `agy_proxy/providers/web_prompt_utils.py`:

1. **IDE System Prompt Compaction (`compact_web_system_prompt`)**: Compresses oversized IDE/agent system prompts so web chat frontends stay fast and within context budgets without losing critical instructions.
2. **Automatic Prompt Restriction Stripping**: Invokes `clean_system_prompt_text` inside `compact_web_system_prompt`, stripping artificial negative constraints (CAPTCHA/bot-check bans, reverse-engineering bans, boilerplate guardrails) across DeepSeek Web, ChatGPT Web, Gemini Web Auth, and Gemini Guest automatically. Set `AGY_PROXY_STRIP_PROMPT_RESTRICTIONS=0` to opt out.
3. **Native `role: "developer"` Support**: Treats OpenAI/Codex `role: "developer"` messages identically to `role: "system"` across full prompts and multi-turn continuation prompts.
4. **Unified Tool Calling (`WebToolCallStreamParser`)**:
   - Formats OpenAI/Anthropic `tools` definitions into compact `dsml` (DeepSeek) or `<tool_call>` (Gemini Guest / ChatGPT Web) instructions.
   - Streams tool calls incrementally as standard `tool_calls` deltas during SSE streaming and aggregates them cleanly for non-streaming unary responses.

---

[← Back to Main README](../README.md)
