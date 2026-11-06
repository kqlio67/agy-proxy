# 🤖 Claude Code CLI Comprehensive Guide

Run Anthropic's official **Claude Code CLI** with maximum freedom, zero telemetry, customizable MCP tools, and either **Gemini 1M context via `agy-proxy`** or **direct official Anthropic API (`--direct`)**.

---

## 📑 Table of Contents
- [Quick Start](#-quick-start)
- [Two Modes of Operation](#-two-modes-of-operation)
  - [Mode 1: Proxy Mode (Gemini 1M Context & Free Quota)](#mode-1-proxy-mode-gemini-1m-context--free-quota)
  - [Mode 2: Direct Vanilla Mode (Official Anthropic API)](#mode-2-direct-vanilla-mode-official-anthropic-api)
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
*Injects proxy endpoints, token, and 1M context limits into `~/.claude/settings.json`, creating a backup `settings.json.agy_backup`.*

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
| `agy-proxy setup-claude` | Persistently configure `~/.claude/settings.json` for proxy. |
| `agy-proxy setup-claude --direct` | Persistently configure `~/.claude/settings.json` for direct official API. |
| `agy-proxy restore-claude` | Restore original `~/.claude/settings.json` from backup. |

---

[← Back to Main README](../README.md)
