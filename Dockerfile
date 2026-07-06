FROM python:3.12-slim AS builder

WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim

ARG VERSION=latest
LABEL org.opencontainers.image.title="RelayCat" \
      org.opencontainers.image.description="Telegram relay and Business chat automation" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RELAYCAT_HOST=0.0.0.0 \
    RELAYCAT_PORT=8765 \
    RELAYCAT_DATA_DIR=/data \
    RELAYCAT_DB_URL=sqlite+aiosqlite:////data/relaycat.db

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels \
    && addgroup --system --gid 10001 relaycat \
    && adduser --system --uid 10001 --ingroup relaycat --home /app relaycat \
    && mkdir -p /data \
    && chown -R relaycat:relaycat /app /data

COPY --chown=relaycat:relaycat app ./app
USER relaycat

VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('RELAYCAT_PORT','8765') + '/healthz', timeout=3)" || exit 1

CMD ["python", "-m", "app.main"]
