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
  ghcr.io/agy-proxy/agy-proxy:latest
```

### Docker Compose
Create a `docker-compose.yml`:
```yaml
services:
  agy-proxy:
    image: ghcr.io/agy-proxy/agy-proxy:latest
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
git clone https://github.com/agy-proxy/agy-proxy.git
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

## 📦 Standalone Binaries (Zero Dependencies)

If Python is not installed on the host system, download compiled standalone single-file executables from the GitHub Releases page:
- `agy-proxy-linux-x86_64`
- `agy-proxy-darwin-arm64` (Apple Silicon)
- `agy-proxy-darwin-x86_64` (Intel)
- `agy-proxy-windows-x64.exe`

Make executable and run directly:
```bash
chmod +x agy-proxy-linux-x86_64
./agy-proxy-linux-x86_64
```

---

[← Back to Main README](../README.md)
