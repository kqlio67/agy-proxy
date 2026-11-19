# ⚙️ Deployment & System Services Guide

Run `agy-proxy` as a persistent background daemon, inside Docker containers, or natively on mobile devices via Termux.

---

## 🐳 Docker & Docker Compose

### Quick Docker Run
```bash
docker run -d \
  --name agy-proxy \
  -p 8000:8000 \
  -v agy_data:/root/.config/agy-proxy \
  ghcr.io/kqlio67/agy-proxy:latest
```

### Docker Compose
Create a `docker-compose.yml`:
```yaml
services:
  agy-proxy:
    image: ghcr.io/kqlio67/agy-proxy:latest
    container_name: agy-proxy
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - agy_data:/root/.config/agy-proxy

volumes:
  agy_data:
```
Launch with:
```bash
docker compose up -d
```

---

## ⚙️ Background System Service (Linux & macOS)

Manage `agy-proxy` as an operating system service (systemd on Linux, launchd on macOS):

```bash
# 1. Install & enable background service:
agy-proxy install-service

# 2. Check service status:
agy-proxy status-service

# 3. Control service lifecycle:
agy-proxy service-stop
agy-proxy service-start
agy-proxy service-restart

# 4. Uninstall service completely:
agy-proxy uninstall-service
```

---

## 📱 Android via Termux

Run `agy-proxy` directly on Android smartphones and tablets:

```bash
# 1. Update Termux and install Python + Git:
pkg update && pkg install python git -y

# 2. Clone and install dependencies:
git clone https://github.com/kqlio67/agy-proxy.git
cd agy-proxy
pip install -e .

# 3. Start proxy:
python -m agy_proxy.cli start --host 0.0.0.0 --port 8000
```
*(Tip: Binding to `0.0.0.0` allows your laptop or PC on the same Wi-Fi network to connect to your phone's proxy!)*

---

## 🔄 In-Place Self Update

Keep `agy-proxy` up to date without losing any configuration or pooled accounts:

```bash
# Check if a new version is available:
agy-proxy check-update

# Download & apply the latest update:
agy-proxy update
```

---

## 📦 Standalone Binaries & One-Line Installers (Zero Dependencies)

For quick automated installation with a live terminal progress bar:
- **Linux / macOS**: `curl -fsSL https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.sh | bash`
- **Windows (PowerShell)**: `irm https://raw.githubusercontent.com/kqlio67/agy-proxy/main/install.ps1 | iex`

If you prefer manual downloads, grab pre-built single-file release archives from the [GitHub Releases](https://github.com/kqlio67/agy-proxy/releases) page (verified with `SHA256SUMS.txt`):
- `agy-proxy-linux-amd64.tar.gz` (Linux x86_64)
- `agy-proxy-linux-arm64.tar.gz` (Linux ARM64 / Raspberry Pi / ARM64 Servers)
- `agy-proxy-darwin-arm64.tar.gz` (macOS Apple Silicon)
- `agy-proxy-windows-amd64.zip` (Windows x64)

Extract, make executable, and run directly:
```bash
tar -xzf agy-proxy-linux-amd64.tar.gz
chmod +x agy-proxy
./agy-proxy start
```

---

[← Back to Main README](../README.md)
