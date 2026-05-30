# ── Build stage: install dependencies ──────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy backend source only
COPY backend/ ./backend/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Data directories are expected to be mounted as volumes
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["python", "-m", "backend.run"]
