FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CHROME_PATH=/usr/local/bin/chromium-agent \
    IN_DOCKER=true \
    ANONYMIZED_TELEMETRY=false \
    BROWSER_USE_CLOUD_SYNC=false \
    DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]" \
 && playwright install --with-deps chromium \
 && ln -s "$(find /ms-playwright -path '*chrome-linux*/chrome' -type f | head -1)" /usr/local/bin/chromium-agent \
 && test -x /usr/local/bin/chromium-agent \
 && useradd --create-home --uid 1000 agent && mkdir -p /data && chown agent /data
COPY examples ./examples
USER agent
VOLUME /data
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz')"
CMD ["browser-agent-server"]
