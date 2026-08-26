# Multi-arch lightweight Python container (amd64, arm64 / Raspberry Pi)
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# Install curl for container health checks
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copy project definition and dependencies first for efficient Docker layer caching
COPY pyproject.toml .

# Install dependencies and project package
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy application source code
COPY . .

# Install current package in editable/live mode
RUN pip install --no-cache-dir -e .

# Expose standard proxy port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://127.0.0.1:8000/health || exit 1

# Start the proxy server
CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "8000"]
