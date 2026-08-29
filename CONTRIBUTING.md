# Contributing to Antigravity Proxy

Thank you for your interest in contributing to **Antigravity Proxy**! 🎉

We welcome contributions of all kinds: bug fixes, new features, model mappings, documentation improvements, and feedback.

---

## 🚀 Getting Started

### 1. Fork and Clone
```bash
git clone https://github.com/kqlio67/agy-proxy.git
cd agy-proxy
```

### 2. Environment Setup
We recommend using [`uv`](https://github.com/astral-sh/uv) or standard Python 3.10+:

```bash
# Using uv (fastest):
uv sync

# Or using standard venv:
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Running the Server Locally
```bash
./start_proxy.sh --port 8000
# or: uv run python main.py --port 8000
```

Open [http://localhost:8000](http://localhost:8000) to view the Web UI Dashboard.

---

## 🧪 Running Tests

Before submitting changes, ensure all tests pass:

```bash
uv run python test_proxy.py
```

---

## 📝 Development Guidelines

1. **Clean Code & Idiom:** Write clean, asynchronous code that matches surrounding patterns.
2. **Multi-Account Safety:** Ensure changes do not break multi-account concurrency (`asyncio.Lock()` per session) or failover logic.
3. **No Hardcoded Secrets:** Never commit API keys, OAuth tokens, or sensitive credentials.

---

## 📦 Pull Request Process

1. **Create a Feature Branch:**
   ```bash
   git checkout -b feature/my-cool-feature
   ```
2. **Make your changes** and verify tests pass.
3. **Commit with Clear Messages:** Follow [Conventional Commits](https://www.conventionalcommits.org/) (e.g. `feat: ...`, `fix: ...`, `docs: ...`).
4. **Push and Open a Pull Request** against the `main` branch.

---

## 💬 Community & Questions

- If you encounter a bug or have a feature request, please open a [GitHub Issue](https://github.com/kqlio67/agy-proxy/issues).
- Be respectful and constructive (see [Code of Conduct](CODE_OF_CONDUCT.md)).
