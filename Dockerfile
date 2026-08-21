FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/relaycat \
    && /opt/relaycat/bin/pip install --no-compile -r requirements.txt

FROM python:3.12-slim

ARG VERSION=latest
LABEL org.opencontainers.image.title="RelayCat" \
      org.opencontainers.image.description="Telegram message relay and abuse protection" \
      org.opencontainers.image.source="https://github.com/yayitinyu/RelayCat" \
      org.opencontainers.image.version="${VERSION}"

ENV PATH=/opt/relaycat/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RELAYCAT_HOST=0.0.0.0 \
    RELAYCAT_PORT=8765 \
    RELAYCAT_DATA_DIR=/data \
    RELAYCAT_DB_URL=sqlite+aiosqlite:////data/relaycat.db

RUN addgroup --system --gid 10001 relaycat \
    && adduser --system --uid 10001 --ingroup relaycat --home /app relaycat \
    && mkdir -p /app /data \
    && chown relaycat:relaycat /app /data

WORKDIR /app
COPY --from=builder /opt/relaycat /opt/relaycat
COPY --chown=relaycat:relaycat app ./app

USER relaycat
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('RELAYCAT_PORT','8765') + '/healthz', timeout=3)" || exit 1

CMD ["python", "-m", "app.main"]
