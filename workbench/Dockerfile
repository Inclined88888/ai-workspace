FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
LABEL org.opencontainers.image.title="ROOKS" \
      org.opencontainers.image.description="ROOKS: a lightweight, self-hosted AI workspace. Your move. ROOKS delivers." \
      org.opencontainers.image.source="https://github.com/Inclined88888/ai-workspace"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data STATIC_DIR=/app/static
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=web /web/dist ./static
RUN useradd --create-home --uid 1000 workbench && mkdir -p /data && chown workbench /data
USER workbench
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
# Forwarding headers are trusted only from inside the container: a client cannot fake its address to get past the
# login and pairing limits or the companion's private network check.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "127.0.0.1"]
