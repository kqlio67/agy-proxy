# ==============================================================================
# ⚡ Antigravity Proxy - Official Dockerfile
# Multi-stage lightweight build for containerized deployments
# ==============================================================================

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY agy_proxy/ agy_proxy/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Final runtime image
FROM python:3.11-slim AS runner

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    mkdir -p /home/appuser/.config/agy-proxy /home/appuser/.gemini/antigravity-cli && \
    chown -R appuser:appuser /home/appuser

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/info')" || exit 1

ENTRYPOINT ["python3", "-m", "agy_proxy.cli"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
