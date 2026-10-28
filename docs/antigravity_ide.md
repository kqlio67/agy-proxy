# 🖥️ Google Antigravity 2.0 & IDE Integration Guide

This guide details how to integrate **Google Antigravity 2.0** and **Antigravity IDE** with `agy-proxy` using the native language server patcher.

---

## 📋 Overview

Antigravity IDE relies on a native Go backend (`language_server`) to communicate with Google's CloudCode and Generative Language APIs. The CLI command `agy-proxy patch` manages this executable with a non-destructive wrapper that intercepts and transparently routes all model traffic through `agy-proxy`.

### 🛡️ Non-Destructive Wrapper Architecture
- **Preserves Original Binary**: Backs up the original compiled ELF/Mach-O binary as `language_server.real`.
- **Startup Health-Check**: Tests `agy-proxy` availability on launch (configurable timeout, default 2 seconds).
- **Background Watchdog**: Monitors proxy health and dynamically toggles routing:
  - If `agy-proxy` is running: traffic routes through local proxy (`http://127.0.0.1:8000`).
  - If `agy-proxy` is offline: gracefully falls back to official Google upstream servers (`https://daily-cloudcode-pa.googleapis.com` and `https://generativelanguage.googleapis.com`).
- **Clean 1-Command Reversion**: Restore original binaries at any time with `agy-proxy patch restore`.

---

## 💻 Supported Environments

### Operating Systems & Architectures
- **Linux**: x86_64, aarch64 / arm64
- **macOS**: Apple Silicon (M1/M2/M3/M4, arm64), Intel (x86_64)
- **Windows**: x64, ARM64 (PowerShell & CMD wrappers)

### Applications & Binary Locations
The patcher automatically discovers and patches `language_server` across common installation paths:
- **Antigravity 2.0**: `/opt/Antigravity/resources/bin/language_server`
- **Antigravity IDE**: `/opt/Antigravity IDE/resources/app/extensions/antigravity/bin/language_server`
- **macOS Applications**: `/Applications/Antigravity.app` and `/Applications/Antigravity IDE.app`
- **Windows**: `%LOCALAPPDATA%\Programs\Antigravity` and `%APPDATA%\Antigravity`

---

## ⚙️ Persistent Path Configuration

Save custom binary or editor root directories to `~/.config/agy-proxy/config.json`:

```bash
# Save explicit binary path:
agy-proxy config --set-ls "/opt/Antigravity/resources/bin/language_server"

# Or save editor installation folder (auto-discovers binary inside):
agy-proxy config --set-editor "/opt/Antigravity IDE"

# View current configured paths:
agy-proxy config --show

# Clear custom configured paths:
agy-proxy config --clear
```

---

## 🚀 Quick Usage

### Linux & macOS

```bash
# Check current patching and process status (auto-detects installation):
agy-proxy patch status

# Check status by pointing to an editor installation directory:
agy-proxy patch status -e "/opt/Antigravity IDE"

# Patch automatically (port 8000, startup wait & watchdog enabled):
agy-proxy patch install -y

# Patch by editor directory or specific binary:
agy-proxy patch install -e "/opt/Antigravity"
agy-proxy patch install -f "/opt/Antigravity/resources/bin/language_server"

# Restore original unmodified binary (clean unpatch):
agy-proxy patch restore -y
```

### Windows (PowerShell & Command Prompt)

```powershell
# Check patch status:
agy-proxy patch status

# Install patch with automated backup and watchdog:
agy-proxy patch install -y

# Restore original unmodified binary:
agy-proxy patch restore -y
```

---

## 📑 Command & Flag Reference

| Flag | Description |
| :--- | :--- |
| `-c, --check` | Check patch status, verify wrapper integrity, and show running process PIDs and listening ports. |
| `-u, --unpatch` | Restore the original `language_server` from backup and remove wrapper. |
| `-f, --file <PATH>` | Directly specify path to `language_server` executable. |
| `-d, --dir <PATH>` | Directory containing `language_server` executable or editor root. |
| `-p, --port <PORT>` | Port where `agy-proxy` is listening (default: `8000` or `$PORT`). |
| `-h, --host <HOST>` | Host where `agy-proxy` is listening (default: `127.0.0.1` or `$HOST`). |
| `--url <URL>` | Full proxy base URL (e.g. `http://127.0.0.1:8080` or Cloudflare Worker URL). |
| `--timeout <SECONDS>` | Max seconds to wait for proxy health-check at startup (default: `2`, `0` to disable). |
| `--no-wait` | Disable startup wait check before executing binary. |
| `--no-watchdog` | Disable background watchdog for live auto-switching (startup fallback only). |
| `-y, --yes` | Automatically answer yes to all confirmation prompts. |

---

[← Back to Main README](../README.md)
