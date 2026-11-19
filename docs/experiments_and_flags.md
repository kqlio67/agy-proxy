# 🧪 Antigravity Experimental Features, Flags & Prompt Sanitizer

Safely unlock hidden Antigravity Hub **v2.33.0** developer features, curate live upstream experiment IDs, shield against toxic internal Google flags, and automatically remove artificial prompt restrictions that degrade model reasoning.

---

## 🌟 Unlocked Features (Antigravity Hub v2.33.0 Curated)

`agy-proxy` aligns client telemetry to `antigravity/hub/2.33.0` (`cl=1023160026`), injects curated live upstream experiment IDs (`CURATED_EXPERIMENT_IDS`), and activates verified stable experimental features by default:

### Core Workspace & Agent Features
- ⚔️ **Battle Mode (`enable-battle-mode`)**: Run multiple models in parallel on the same prompt to compare answers side-by-side.
- 🤖 **Autonomous Mode (`labs-autonomous-mode`, `jetski-autonomous-mode`, `enroll-new-users-in-auto-mode`)**: Grants autonomous execution without repeated confirmation prompts for safe tasks.
- 💬 **Inline Actions (`labs-inline-actions`)**: Floating toast notification cards to answer questions and grant permissions without switching tabs.
- 🧠 **Thought Steering & Editing (`enable-thought-editing`, `enable-thought-steering-from-selection`)**: Edit or redirect model internal reasoning chains directly in the chat UI.
- 📁 **File Explorer Tab (`enable-file-explorer-tab`)**: Native side panel file tree inside Web Hub.
- 🪟 **Split View (`enable-split-view`)**: Side-by-side multi-conversation panes.
- 🔍 **Conversation Search V2 (`enable-conversation-search-v2`)**: Fast full-text search across all past chat sessions.

### New in v2.33.0 (`agy-proxy` v2.3.0)
- 🌐 **SSH Remote & CUA Script Mode (`enable-ssh-remote`, `enable-agy-hub-cua-script-mode`)**: Unlocks SSH remote development environments and Computer Use Agent (CUA) script execution mode.
- 🖥️ **Browser Subagent Webview & Remote Wake (`enable-browser-subagent-webview`, `enable-remote-preview-wake`)**: Embedded browser subagent preview rendering and automatic wake for remote web previews.
- 🧱 **Workspace UI Redesign (`enable-wcv-three-layer-stack`, `enable-aux-pane-redesign-extension`, `enable-work-efficiently-reminder`)**: Enables the 3-layer Workspace Context View stack and redesigned auxiliary extension panes.
- 🧠 **Ambient RAG & Custom Preambles (`ambient_injection_enabled`, `DuetAi__custom_preambles`, `DuetAiLocalRag__merge_fragments`, `DuetAICodeTransform__custom_slash_commands`)**: Merges fragmented local RAG results and enables custom preambles and transform slash commands.
- 🗄️ **Database & Skills Migration (`DatacloudDatabases__discoverability_improvements`, `DatacloudDatabases__enable_query_execution_details`, `DuetAiGeneration__auto_trigger_on_empty_class_struct_def`, `deprecate-workflows`)**: Enhances database query execution inspection and legacy workflow-to-skill migration.

---

## 🧹 Default Prompt Restriction Stripping & Guidelines Curation

Starting in `v2.3.0`, `agy-proxy` automatically strips artificial injected restrictions and boilerplate guardrails by default across **CloudCode** (`generateContent` & `streamGenerateContent`), **all Web Providers** (`DeepSeek Web`, `ChatGPT Web`, `Gemini Web`, `Gemini Guest`), and **Cloudflare Workers**:

- **Removes Artificial Constraints**: Strips v2.33.0 CAPTCHA / bot-check prohibitions, platform automation / web scraping blocks, `Access Control Weakening` boilerplate, reverse-engineering bans, sandbox negative-constraint injections, and catalog/tool promotional bloat that degrade reasoning and trigger false refusals.
- **Curates `fetchAvailableModels` Templates**: Automatically sanitizes `template__system_prompts__guidelines` returned by `fetchAvailableModels` so Antigravity `language_server` starts with clean guidelines at model initialization.
- **Cleans Both System & User-Injected Guidelines**: Sanitizes `systemInstruction` as well as guideline-injected `contents` blocks.
- **Opt-Out Switch**: To disable restriction stripping and preserve raw upstream prompts, set:
  ```bash
  export AGY_PROXY_STRIP_PROMPT_RESTRICTIONS=0   # or false / off / disable
  ```

---

## 🛡️ Toxic Flag Blocklist (Protection against breakage)

Certain experimental flags built into Google's internal code require Google corporate infrastructure (`blaze run`, Google3 Piper depot paths, internal microservices). Enabling them outside of Google breaks the application.

`agy-proxy` automatically shields your installation by blocklisting:
- `enable-agent-team`: Calls missing `blaze run //third_party/jetski/...` scripts.
- `enable-concierge-agent`, `labs-concierge-agent`: Calls missing `/google/data/.../concierge_main.par` binaries.
- `enable-piper-depot`: Crashes when Google's internal Piper VCS is absent.
- `enable-internal-telemetry`: Blocks hanging internal analytics network calls.

---

## ⚙️ CLI & REST API Management

```bash
# List all experimental flags and current status:
agy-proxy flags

# Force enable a specific flag:
agy-proxy flags --enable "enable-battle-mode"

# Disable a specific flag:
agy-proxy flags --disable "enable-thought-editing"

# Change operating mode (curated [default], all_safe, passthrough):
agy-proxy flags --mode curated

# Reset all overrides back to curated defaults:
agy-proxy flags --reset
```

---

[← Back to Main README](../README.md)
