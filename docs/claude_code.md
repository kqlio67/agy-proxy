# 🤖 Claude Code CLI Comprehensive Guide

Run Anthropic's official **Claude Code CLI** with maximum freedom, zero telemetry, customizable MCP tools, and either **Gemini 1M context via `agy-proxy`** or **direct official Anthropic API (`--direct`)**.

---

## 📑 Table of Contents
- [Quick Start](#-quick-start)
- [Two Modes of Operation](#-two-modes-of-operation)
  - [Mode 1: Proxy Mode (Gemini 1M Context & Free Quota)](#mode-1-proxy-mode-gemini-1m-context--free-quota)
  - [Mode 2: Direct Vanilla Mode (Official Anthropic API)](#mode-2-direct-vanilla-mode-official-anthropic-api)
- [Anthropic Next-Gen Models & Reasoning Effort (`claude-5`, `opusplan`, `--effort`)](#-anthropic-next-gen-models--reasoning-effort)
- [Experimental Code Mode & REPL Architecture (`--code-mode`)](#-experimental-code-mode--repl-architecture)
- [Standalone Binary Patcher & Debloater (`patch-claude`)](#-standalone-binary-patcher--debloater-patch-claude)
- [Selective MCP Tool Management](#-selective-mcp-tool-management)
- [Autonomous Bypass Mode (`--bypass` / `--danger`)](#-autonomous-bypass-mode---bypass----danger)
- [Internal Fixes & Optimizations](#-internal-fixes--optimizations)
- [Persistent Configuration (`setup-claude`)](#-persistent-configuration-setup-claude)
- [CLI Reference Summary](#-cli-reference-summary)

---

## ⚡ Quick Start

```bash
# 1. Ephemeral proxy session with Gemini 3.8 Flash High (1M context):
agy-proxy claude

# 2. Ephemeral direct official Anthropic session (debloated, zero telemetry):
agy-proxy claude --direct

# 3. Patch installed Claude Code binary to remove hardcoded prompt restrictions:
agy-proxy patch-claude apply

# 4. Run fully autonomous without permission prompts:
agy-proxy claude --danger
```

---

## 🔄 Two Modes of Operation

### Mode 1: Proxy Mode (Gemini 1M Context & Free Quota)

Routes Claude Code requests through `agy-proxy` to Google CloudCode / Gemini endpoints.

```bash
# Default recommended model (gemini-3.8-flash-high):
agy-proxy claude

# Specific model variants:
agy-proxy claude -m claude-sonnet-4-6
agy-proxy claude -m claude-opus-4-6-thinking
agy-proxy claude -m gemini-3.8-pro

# Resume last conversation in current directory:
agy-proxy claude -c

# Pass prompt directly in non-interactive print mode:
agy-proxy claude -p "Refactor auth_handler.py"
```

**Key Advantages in Proxy Mode:**
- **1,000,000 Token Context Window**: `CLAUDE_CODE_MAX_CONTEXT_TOKENS=1000000`.
- **First-Party Base URL Assumption**: Sets `_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL: "1"`, bypassing Claude Code's 3P degradation check. This prevents third-party feature clipping, enables 1-hour prompt caching, unlocks next-gen model families (Fable/Mythos), and eliminates artificial rate-limiting heuristics.
- **High-Capacity Compaction**: Automatic context compaction threshold scaled up to **800,000 tokens** (instead of the standard 160k cutoff).
- **Prompt Sanitization**: Strips Anthropic internal billing headers (`x-anthropic-billing-header`) and anti-clone rules before forwarding to Gemini.

---

### Mode 2: Direct Vanilla Mode (Official Anthropic API)

If you or your team prefer connecting directly to `api.anthropic.com` using your official Anthropic API key, but want all privacy, debloating, and performance perks:

```bash
# Launch direct session with official API:
agy-proxy claude --direct

# Launch direct with specific Anthropic model:
agy-proxy claude --direct -m claude-opus-4-6-thinking
```

**What `--direct` does automatically:**
- **Zero Proxy Overhead**: Removes `ANTHROPIC_BASE_URL` so traffic goes straight to `api.anthropic.com`.
- **Zero Telemetry**: Injects `DISABLE_TELEMETRY=1` and `DISABLE_ERROR_REPORTING=1`.
- **Blocks Remote GrowthBook Config**: Injects `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, preventing Anthropic from remotely pushing dynamic restrictions (`tengu_*`) and nag popups.
- **Fast Startup**: Injects `CLAUDE_CODE_MCP_SKIP_UNCHANGED_RELIST=1` to skip redundant MCP schema polling on every turn.

---

## 🧠 Anthropic Next-Gen Models & Reasoning Effort

Discovered via deep REA reverse engineering of Claude Code CLI v2.1.325, Anthropic embeds official model identifiers and thinking tiers. `agy-proxy` provides first-class support and seamless mapping across both local proxy routes and Cloudflare Workers:

### 1. Supported Next-Gen Model Families

| Model ID | Display Name | Thinking Tier | Native Context | Description |
| :--- | :--- | :--- | :--- | :--- |
| `claude-sonnet-5` / `claude-sonnet-5-5` | Claude Sonnet 5 / 5.5 | High | 200k / 1M | Anthropic next-gen flagship coding model |
| `claude-opus-5` / `claude-opus-5-5` | Claude Opus 5 / 5.5 | High / Max | 200k / 1M | Anthropic frontier deep reasoning and architecture model |
| `claude-fable-5` / `claude-fable-5-2` | Claude Fable 5 / 5.2 | High / Tier 3 | 200k / 1M | Specialized autonomous agent planning and verification model |
| `claude-mythos-5` / `claude-mythos-5-2` | Claude Mythos 5 / 5.2 | Max / Tier 3 | 1M native | Frontier flagship intelligence model with maximum depth |
| `claude-haiku-5-5` | Claude Haiku 5.5 | Low / Adaptive | 1M native | Ultra-efficient high-speed model |
| `opusplan` | OpusPlan | High | 200k / 1M | Hybrid preset: Opus 5.5 for architecture + Sonnet 4.6 for execution |

```bash
# Launch with Claude Sonnet 5:
agy-proxy claude -m claude-sonnet-5

# Launch with OpusPlan hybrid preset:
agy-proxy claude -m opusplan

# Launch with Claude Fable 5.2 autonomous planner:
agy-proxy claude -m claude-fable-5-2
```

### 2. Adaptive Reasoning Effort (`--effort`)

Claude Code CLI supports adjusting reasoning depth using `--effort` (or `-e`):
- `low`: Minimal thinking overhead, optimal for routine edits and syntax fixes.
- `medium`: Balanced reasoning for refactoring and debugging.
- `high`: Deep reasoning budget (equivalent to standard thinking).
- `xhigh` / `max`: Maximum reasoning depth for frontier architectural challenges.

```bash
# Run with maximum thinking budget:
agy-proxy claude --effort max

# Combine custom model and effort:
agy-proxy claude -m claude-opus-5-5 --effort max -p "Design distributed consensus protocol"
```

In `agy-proxy`, the `effort` parameter is captured from the Anthropic request payload and mapped into Google CloudCode's thinking budget and temperature configuration automatically.

---

## ⚡ Experimental Code Mode & REPL Architecture

In Claude Code **v2.1.323+**, Anthropic added **Code Mode** (`CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`). Instead of the traditional multi-turn loop where each file read, edit, or command consumes an entire LLM round-trip and pollutes context, Claude writes a short JavaScript program with `top-level await`. The script runs locally inside a sandboxed Bun VM context, executing tools in parallel and returning only the concise final result (`<repl-output>`).

### 1. Key Benefits
- **Drastic Token Savings**: Cuts token usage by **80%–98%** (e.g. from 150,000 tokens down to 2,000 tokens on multi-file inspections).
- **Reduced Round-Trips**: A batch of file operations executes in 1 turn instead of 10–20 turns.
- **Inspect Generated Scripts**: Press `Ctrl+O` in interactive mode to view the JavaScript script Claude wrote.

### 2. How to Enable in `agy-proxy`

```bash
# Launch ephemeral session with Code Mode:
agy-proxy claude --code-mode

# Or persistently configure in ~/.claude/settings.json:
agy-proxy setup-claude --code-mode
```

*When enabled via `agy-proxy`, `debloat_claude_json` automatically marks `codeModeCaveatsAcceptedV1: true` and `codeModeAutoModeCaveatAcceptedV1: true` in `~/.claude.json`, skipping the intrusive onboarding confirmation prompt.*

### 3. Requirements & Gate Checks (REA Breakdown)

From reverse engineering the Claude Code binary (`chunk-rrq84hjd.js`), Code Mode enforces strict conditions:
- **First-Party Assumption (`A()`)**: Claude Code normally refuses to run Code Mode over custom proxies (`ANTHROPIC_BASE_URL != api.anthropic.com`) with `why: "provider"`. **`agy-proxy` bypasses this** via `_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL="1"`, allowing Code Mode to work seamlessly!
- **Model Family (`Vxn()`)**: Code Mode requires model version $\ge$ 5.5 (`claude-sonnet-5-5`, `claude-opus-5-5`, `claude-haiku-5-5`) or $\ge$ 5.2 for Fable/Mythos (`claude-fable-5-2`, `claude-mythos-5-2`). Older models trigger `why: "model"`.
- **Interactive Terminal Only (`ol() === "cli"`)**: Disabled in print mode (`claude -p`), VS Code extension, and Desktop app.
- **GrowthBook Flag Bypass**: Gated behind `tengu_rustling_blossom`. `agy-proxy patch-claude` and `setup-claude` automatically inject this flag into `cachedGrowthBookFeatures` so you don't have to wait for Anthropic's server rollout waves.
- **Bash Dependency**: Requires `bash` available in `PATH` (on Windows, Git for Windows is required).

### 4. Achieving Code Mode in Gemini / Antigravity ("Prompt-Based REPL")

While Antigravity / Gemini does not have a hardcoded JS REPL sandbox in the CLI, the exact same token-saving behavior is achieved with a simple instruction:

> *"Do not read files one by one. Write a single Python or Node.js script that performs all operations in parallel, filters the data, and prints only the final summary to stdout. Execute it and provide your response based on the output."*

- **Demo Task (Scan 34 Markdown files for broken links)**:
  - Standard loop: 11 turns, 2m 8s, 46k tokens.
  - Single-script prompt: 3 turns, 37s, 24k tokens (3.5x faster, 50% fewer tokens).
- *Note: Does not work with `--sandbox` if the sandbox blocks interpreter execution.*

---

## 🛠 Standalone Binary Patcher & Debloater (`patch-claude`)

Anthropic embeds artificial prompt limitations and fallback constraints directly into the Claude Code standalone binary, including:
- **`tengu_heron_brook` (Anti-clone game limitation)**: Hardcoded 265-byte directive prohibiting recreations of existing games, mechanics, or genre tropes.
- **Nag Tips & Up-sells**: 27 intrusive tip banners (`plan-mode`, `workflow-size-prompting`, `subagent-fanout-nudge`, etc.) tracked in `~/.claude.json`.

`agy-proxy patch-claude` allows you to inspect, patch, and debloat your local Claude Code installation non-destructively:

### 1. Check Patch Status
```bash
agy-proxy patch-claude status
```
*Output displays detected binary location, whether it is patched, and if a backup exists.*

### 2. Apply Binary Patch & Debloater
```bash
agy-proxy patch-claude apply
```
*Actions performed:*
1. Locates Claude Code binary (e.g. `~/.local/share/claude/versions/2.1.317`).
2. Creates an atomic backup copy at `<binary>.agy_orig`.
3. Replaces the 265-byte restriction string with whitespace, maintaining exact binary offsets, V8/Bun bytecode integrity, and executable checksums.
4. Updates `~/.claude.json` to silence all 27 nag-tips (`tipsHistory`), while leaving `autoUpdates` untouched.

### 3. Restore Original Binary
```bash
agy-proxy patch-claude restore
```
*Restores the original unmodified binary from `<binary>.agy_orig`.*

### 4. Debloat Settings Only (Without Modifying Binary)
```bash
agy-proxy patch-claude debloat
```

---

## 🔌 Selective MCP Tool Management

External Model Context Protocol (MCP) servers configured in `~/.claude.json` can inject tens of thousands of tokens of tool schemas into every single request (often consuming 80k–100k tokens before you even type your prompt).

`agy-proxy` provides granular MCP control:

```bash
# 1. List installed MCP servers found in ~/.claude.json and project .mcp.json:
agy-proxy claude --list-mcp

# 2. Disable all external MCP servers (saves ~90,000 tokens per prompt):
agy-proxy claude --no-mcp
# or:
agy-proxy claude --mcp none

# 3. Selectively load only specific tools by name (comma-separated):
agy-proxy claude --mcp rea
agy-proxy claude --mcp "fast-browser, rea"

# 4. Use all installed MCP tools (default):
agy-proxy claude --mcp all
```

*When selective MCP mode is used, `agy-proxy` creates an isolated ephemeral config and passes `--strict-mcp-config --mcp-config <temp>`, automatically cleaning up temporary files upon exit.*

---

## 🚀 Autonomous Bypass Mode (`--bypass` / `--danger` / `--extra-danger`)

Control automation levels with granular safety guards:

```bash
# Standard auto-approve (skips routine permission prompts, but preserves safety confirmation for rm -rf / destructive commands):
agy-proxy claude --danger
# aliases: --bypass, --yolo, --dangerously-skip-permissions

# Full unattended mode (suppresses confirmation prompts even for destructive operations via CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS):
agy-proxy claude --extra-danger
# alias: --nuclear
```

**Safety Boundaries:**
- `--danger`: Passes `--dangerously-skip-permissions` to Claude Code CLI. Filesystem reads/writes, edits, and standard bash commands run automatically, but Claude's built-in safeguard prompts for recursive deletions (`rm -rf`) remain active.
- `--extra-danger`: Activates `CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS=1` for completely unattended CI/CD headless execution where no terminal interactions are possible.

---

## 🛡 Internal Fixes & Optimizations

`agy-proxy` resolves several known issues when running Claude Code:

| Issue | Root Cause | `agy-proxy` Resolution |
| :--- | :--- | :--- |
| **HTTP 429 RESOURCE_EXHAUSTED** | Anthropic sends `x-anthropic-billing-header` in system prompts, which Google CloudCode explicitly blocks. | `clean_system_prompt_text` automatically strips billing headers. |
| **Infinite `/compact` Loops** | Claude Code prepends local `<command-name>/compact</command-name>` history into user turns, confusing summarizer detectors. | `is_explicit_compact_request` accurately distinguishes true compaction prompts from conversation history logs. |
| **HTTP 500 Role Rejection** | Anthropic alternating turn rules differ from Google CloudCode expectations. | Roles are automatically normalized (`user`/`model`), thought signatures are preserved, and empty blocks are sanitized. |
| **Nag Tips & Up-sells** | Local `~/.claude.json` triggers recurring pop-up hints. | Debloater sets tip history counters to maximum to silence nagging prompts. |

---

## ⚙️ Persistent Configuration (`setup-claude`)

If you prefer running the standard `claude` command directly from your terminal rather than typing `agy-proxy claude`:

### Configure to route through `agy-proxy`:
```bash
agy-proxy setup-claude --port 8000 --model anthropic.gemini-3.8-flash-high
```
*Injects proxy endpoints, token, 1M context limits, first-party assumption (`_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL: "1"`), default Fable & Opus mappings, and interactive `/model` picker options with all next-gen models into `~/.claude/settings.json`, creating a backup `settings.json.agy_backup`.*

### Configure for direct official Anthropic API (Debloated):
```bash
agy-proxy setup-claude --direct
```
*Cleans proxy settings while ensuring telemetry is disabled and remote nudges are blocked in `settings.json`.*

### Restore original settings:
```bash
agy-proxy restore-claude
```

---

## 📋 CLI Reference Summary

| Command | Description |
| :--- | :--- |
| `agy-proxy claude` | Launch Claude Code in ephemeral proxy mode (Gemini 1M context). |
| `agy-proxy claude --code-mode` | Launch Claude Code with experimental Code Mode (JS REPL). |
| `agy-proxy claude -m <model>` | Launch with custom model (e.g. `claude-sonnet-5`, `opusplan`, `claude-fable-5-2`). |
| `agy-proxy claude --effort <level>` | Set reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`). |
| `agy-proxy claude --direct` | Launch Claude Code directly against official Anthropic API (debloated). |
| `agy-proxy claude --mcp <name>` | Selectively load specific MCP servers (e.g. `--mcp rea`). |
| `agy-proxy claude --no-mcp` | Launch without MCP tools to conserve ~90k context tokens. |
| `agy-proxy claude --danger` | Auto-approve routine actions (preserves confirmation for destructive rm -rf commands). |
| `agy-proxy claude --extra-danger` | Full unattended mode (also suppresses confirmation for destructive operations). |
| `agy-proxy claude -c` | Continue the most recent conversation in the directory. |
| `agy-proxy patch-claude status` | Check binary patch status and backup existence. |
| `agy-proxy patch-claude apply` | Apply anti-restriction binary patch and debloat tips. |
| `agy-proxy patch-claude restore` | Restore original Claude Code binary from backup. |
| `agy-proxy patch-claude debloat` | Debloat `~/.claude.json` without modifying the binary. |
| `agy-proxy setup-claude` | Persistently configure `~/.claude/settings.json` for proxy (with first-party bypass). |
| `agy-proxy setup-claude --code-mode` | Persistently enable experimental Code Mode in `settings.json`. |
| `agy-proxy setup-claude --direct` | Persistently configure `~/.claude/settings.json` for direct official API. |
| `agy-proxy restore-claude` | Restore original `~/.claude/settings.json` from backup. |

---

[← Back to Main README](../README.md)
