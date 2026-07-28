# ── Stage 1: Build React Frontend ─────────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --production=false

COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python Backend ───────────────────────────────────────────────────
FROM python:3.12-slim

# System dependencies for numpy and process management
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libgomp1 curl && \
    rm -rf /var/lib/apt/lists/*

# Set IST timezone for log readability (logic already uses pytz)
ENV TZ=Asia/Kolkata
RUN ln -sf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime

WORKDIR /app

# ── Install Python dependencies (Docker layer cache) ─────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy application code ────────────────────────────────────────────────────
COPY run.py .
COPY backend/ backend/

# ── Copy pre-built frontend from Stage 1 ─────────────────────────────────────
COPY --from=frontend-builder /app/frontend/dist/ frontend/dist/



# ── Non-root user for security ───────────────────────────────────────────────
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# ── Port configuration ──────────────────────────────────────────────────────
# Default: 8000 for DigitalOcean / Docker deployments
ENV PORT=8000
EXPOSE ${PORT}

# ── Healthcheck ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/status || exit 1

# ── Entrypoint ───────────────────────────────────────────────────────────────
# Credentials must be supplied via Docker env vars (-e) or secrets:
#   -e DHAN_CLIENT_CODE=...
#   -e DHAN_ACCESS_TOKEN=...
#   -e TELEGRAM_BOT_TOKEN=...
#   -e TELEGRAM_CHAT_ID=...
#
# Runs under watchdog.py rather than uvicorn directly: watchdog monitors the
# app's heartbeat, auto-restarts it on crash or freeze, and logs restart
# events to backend/logs/watchdog.log — separate from app.log so a crash
# always leaves something to look back at. Exec form (not shell string) so
# `docker stop` sends SIGTERM straight to watchdog.py's own graceful-shutdown
# handler instead of being swallowed by an intermediate shell.
CMD ["python", "backend/watchdog.py"]
