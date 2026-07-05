# ============================================================
# LifeVault — Dockerfile
# ============================================================
# Multi-stage build for a lean production image.
# Demonstrates the "Deployability" course concept.
#
# Build:   docker build -t lifevault .
# Run:     docker run -p 8000:8000 -e GOOGLE_API_KEY=... lifevault
# ============================================================

# --------------- Stage 1: Builder ---------------
FROM python:3.12-slim AS builder

WORKDIR /app

# Install system deps needed for cryptography wheel
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --------------- Stage 2: Runtime ---------------
FROM python:3.12-slim

LABEL maintainer="Yash Kawade <yash.p.kawade@gmail.com>"
LABEL description="LifeVault — Secure Personal Life Management Concierge"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY mcp_server/ ./mcp_server/
COPY agents/ ./agents/
COPY tests/ ./tests/
COPY cli.py .
COPY demo_data.py .
COPY requirements.txt .
COPY .env.example .
COPY README.md .

# Create directories for runtime data
RUN mkdir -p /app/vault_data /app/generated_qr

# Environment defaults (override at runtime)
ENV PYTHONUNBUFFERED=1 \
    GOOGLE_GENAI_MODEL=gemini-2.5-flash \
    GOOGLE_GENAI_USE_VERTEXAI=FALSE \
    VAULT_DB_PATH=/app/vault_data/vault.db \
    MCP_SERVER_PORT=8080

# Expose ADK web UI port
EXPOSE 8000

# Health check — verifies ADK web server responds
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000')" || exit 1

# Run the ADK web UI
CMD ["adk", "web", "--port", "8000", "--host", "0.0.0.0", "agents/"]
