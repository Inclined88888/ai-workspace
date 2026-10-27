# ROOKS runner: where the assistant's terminal commands and file edits happen. It sees only the workspace volume:
# no ROOKS database, no APP_SECRET, no API keys.
FROM python:3.12-slim
# Extra system packages (the runner has no sudo, so add them here): RUNNER_PACKAGES in .env, then ./setup.sh
ARG RUNNER_PACKAGES=""
RUN case " $RUNNER_PACKAGES" in *[!a-zA-Z0-9.+:=\ -]*|*" -"*) echo "RUNNER_PACKAGES may only list package names" >&2; exit 1 ;; esac \
    && apt-get update && apt-get install -y --no-install-recommends bash git curl ca-certificates procps less $RUNNER_PACKAGES \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1001 runner && mkdir -p /workspace && chown runner /workspace
COPY runner.py /opt/rooks/runner.py
USER runner
ENV WORKSPACE=/workspace RUNNER_MODE=container PYTHONUNBUFFERED=1
VOLUME /workspace
EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health')"
CMD ["python", "/opt/rooks/runner.py"]
