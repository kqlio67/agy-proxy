# 🚀 Antigravity Web Mode & Patched Web Hub

Run Google Antigravity 2.0 directly in your browser with zero Electron overhead, saving RAM and unlocking hidden developer features.

---

## 📊 Overview & Resource Optimization

Google Antigravity 2.0 features an ultra-fast, native Go backend (`language_server`) hosting its full IDE and Agent web interface locally on port `44741`. The desktop application normally runs this inside an Electron wrapper (`/opt/Antigravity/antigravity`), which consumes significant RAM and spawns 10–15 background Chromium helper processes.

Antigravity Web Mode runs `language_server` directly in standalone mode, allowing you to use the full Antigravity 2.0 Web UI directly in your favorite browser (Helium, Chrome, Chromium, Firefox, Brave, Edge).

### 📈 Resource Benchmark

| Metric | Desktop App (Electron) | Web Mode (No-Electron) | Improvement |
| :--- | :--- | :--- | :--- |
| **RAM Footprint** | High | **Low** | **Significant RAM freed!** |
| **CPU Queue Load** | 8.0+ (Thermal throttling on 2-core CPUs) | **~2.0 (Smooth & Cool)** | **~75% CPU load drop** |
| **Process Count** | 10–15 Chromium processes | **1 lightweight Go binary** | Zero Electron bloat |
| **Feature Parity** | 100% | **100% (Identical)** | Subagents, chat, files, git, terminal |

---

## 🏃 Standard Web Mode (`agy-proxy web`)

```bash
# Start Antigravity in browser Web mode (terminal link printed, browser not opened by default):
agy-proxy web

# Check current Web mode status:
agy-proxy web --status

# Automatically close running Electron window to free memory:
agy-proxy web -k

# Open browser automatically on startup (-o / --open):
agy-proxy web -k -o

# Custom port or custom browser:
agy-proxy web --port 44741 --browser helium

# Download standalone language_server binary on a server without Antigravity installed:
agy-proxy web --download-server
```

> [!TIP]
> **Tab Spam Protection**: By default, `agy-proxy web` does **not** auto-open your browser to prevent accidental browser tab spam when running multiple background commands. You can click the displayed HTTPS URL directly in your terminal, or pass `-o` / `--open`. When `-o` is used, a 10-second cross-process debounce lock prevents duplicate tabs from opening.

### Combined One-Command Launch (Proxy + Web together)
```bash
# Starts agy-proxy and launches Antigravity Web simultaneously:
./start_proxy.sh --web
```

> [!NOTE]
> **Local SSL Certificate**: Because Antigravity serves local HTTPS with self-signed certificates, your browser will prompt to accept the certificate on first access. Simply click **Advanced** -> **Proceed to 127.0.0.1**.

---

## 📦 Headless & VPS Server Setup (`--download-server`)

You can run Antigravity Web Mode on headless Linux servers, VPS, remote containers, or machines without the full Antigravity desktop Electron app installed.

### Automatic Binary Resolution
When you run `agy-proxy web`, it searches for `language_server` in the following order:
1. Explicit CLI arguments (`--ls-path` or `-e / --editor`)
2. Environment variables (`ANTIGRAVITY_LS_PATH`, `ANTIGRAVITY_EDITOR_PATH`)
3. User config (`~/.config/agy-proxy/config.json`)
4. Cached download directory (`~/.config/agy-proxy/bin/language_server`)
5. Standard desktop install locations (`/opt/Antigravity/resources/bin/`, `/opt/Antigravity IDE/...`)
6. System `PATH`
7. **Auto-Download prompt** (if running in interactive terminal): automatically offers to download the official Google release for your OS/architecture and extracts the binary into `~/.config/agy-proxy/bin/`.

### Manual Pre-Download
To download and extract the binary beforehand without starting the server:
```bash
agy-proxy web --download-server
```

**Supported extraction formats**:
- **Linux** (`linux-x64`, `linux-arm`): downloads official `Antigravity.tar.gz` and extracts `language_server` using Python's native `tarfile` module (zero external dependencies).
- **macOS** (`darwin-arm` Silicon, `darwin-x64` Intel): downloads `Antigravity.dmg` and extracts `language_server` using `7z` or native `hdiutil`.
- **Windows** (`windows-x64`, `windows-arm`): downloads `Antigravity-x64.exe` (NSIS installer) and extracts `language_server.exe` using `7z`.

---

## 🧪 Patched Web Hub (`agy-proxy web --patched`)

By default, the official Web UI limits certain advanced features to Google internal domains (`jetski.corp.google.com`) and displays non-functional Google3 Piper banners.

Using `agy-proxy web --patched` (or `agy-proxy web -P`) automatically extracts the embedded web assets (304 files) directly from `language_server`, applies clean client-side feature patches, and runs `language_server` with `--web_bundle_path` without modifying the original binary.

### 🌟 Unlocked Features

- 🔓 **Web Terminal Unlocked**: Completely bypasses the corporate domain lock (`restrictToCorpDomain: false`), allowing full bash terminal sessions directly within your browser.
- 📁 **File Explorer & Tabs**: Enables the native file tree and file browser side panel (`enable-file-explorer-tab`).
- ⚔️ **Battle Mode & Split View**: Enables multi-model side-by-side comparison and split chat layouts.
- 🤖 **Autonomous Mode**: Enables the autonomous execution toggle (`labs-autonomous-mode`) without repeated permission prompts.
- 💬 **Inline Actions & Thought Steering**: Displays floating toast action cards and allows editing agent thoughts in real-time.
- 🎯 **Full Model Selector Unlocked**: Direct access to all Google models categorized neatly with section banners:
  - **Default**: Default recommended workspace models (Gemini 3.8 Flash).
  - **Web**: Gemini 4 Argon (Web), Gemini 3.8 Flash (Web), Gemini 3.1 Pro (Web), Gemini 3.5 Flash-Lite (Web) [shown only when running with proxy].
  - **Antigravity**: Gemini 4 Argon (High/Med/Low), Claude Sonnet 5.5 (High/Med/Low), Claude Opus 5.5 (High/Med/Low), Gemini 3.8 Flash (DeepAgent & Tiered), Claude Sonnet 4.6 & Opus 4.6 (Thinking), GPT-OSS 120B, Gemini 3.7 Flash, Gemini 3.6 Flash, Gemini 3.5 Flash/Lite, Gemini 3.1 Pro (High/Med/Low), Gemini 3 Flash, Gemini 2.5 Pro/Flash/Thinking [always visible in both direct and proxy modes].
  - **Fast Completion (Unlimited)**: Tab Flash Lite Preview [Unlimited], Tab Jump Prediction [Unlimited].
  - **Image Generation**: Gemini 3.1 Flash (Image) (with full multimodal image attachments and markdown image rendering).
- 🧹 **Google3 Clutter Removed**: Suppresses scary Google3 chat migration banners (`projectV5Migration`), broken `Agent Team` blaze calls, and inaccessible `Chief of Staff` concierge archives.

### 🚀 Usage

```bash
# 1-click run (auto-extracts bundle and patches UI):
agy-proxy web --patched

# Run with automatic Electron cleanup (-k) and browser launch (-o):
agy-proxy web --patched -k -o

# Restart server (kills previous instances on port):
agy-proxy web --patched -r

# Run on custom port:
agy-proxy web --patched -p 44742

# Point to custom language_server binary:
agy-proxy web --patched --ls-path /opt/Antigravity/resources/bin/language_server

# Force re-extracting and re-patching assets:
agy-proxy web --patched -f

# Show verbose language_server console output (debug mode):
agy-proxy web --patched -v
```

### 📋 Command Options for `agy-proxy web`

| Option | Description |
| :--- | :--- |
| `-P, --patched` | Launch with feature-patched Web UI bundle (unlocks Web Terminal, File Explorer, Battle Mode). |
| `-k, --kill-electron` | Automatically terminate running Electron instances to free RAM. |
| `-o, --open` | Open web browser on startup (default: `False` to prevent tab spam). |
| `--no-open` | Explicitly prevent opening web browser on startup. |
| `-D, --direct, --no-proxy` | Bypass proxy and connect directly to official Google backend. |
| `--download-server` | Download official standalone `language_server` binary for current OS/arch to `~/.config/agy-proxy/bin/` and exit. |
| `-p, --port <port>` | HTTPS port for Antigravity Web UI (default: `44741`). |
| `--host <host>` | Host address to bind Web UI (default: `127.0.0.1`). |
| `--proxy-url <url>` | Proxy backend URL (default: `http://127.0.0.1:8000`). |
| `-r, --restart` | Force restart Antigravity Web even if already running on port. |
| `-s, --status` | Check real-time status of Antigravity Web server and RAM savings. |
| `--detach` | Run Antigravity Web in background daemon mode. |
| `-f, --force-patch` | Force re-extraction and re-patching of the Web UI bundle. |
| `-d, --bundle-dir <path>` | Target directory for extracted bundle (default: `~/.config/agy-proxy/web_ui`). |
| `--ls-path, --bin <path>` | Explicit path to `language_server` binary. |
| `-e, --editor <path>` | Path to Antigravity editor root directory. |
| `-b, --browser <cmd>` | Custom browser executable to open (e.g. `helium`, `google-chrome`, `firefox`). |
| `-v, --verbose, --debug` | Show raw `language_server` debug output in console (default: logged to file). |
| `-h, --help` | Show help message. |

---

[← Back to Main README](../README.md)
