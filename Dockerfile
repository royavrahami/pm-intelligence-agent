# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies (needed for lxml, pydantic)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY . .

# Runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

# Create persistent directories
RUN mkdir -p /app/data /app/reports /app/logs

# Ensure pip user installs are on PATH
ENV PATH=/root/.local/bin:$PATH

# Run as non-root for container security
RUN useradd -m -u 1001 pmAgent && chown -R pmAgent:pmAgent /app
USER pmAgent

# Default command: run the scheduler in daemon mode
CMD ["python", "main.py", "schedule"]
