# Build from the repository root: docker build -f services/api/Dockerfile .
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/
COPY services/api/pyproject.toml services/api/
COPY services/orchestrator/pyproject.toml services/orchestrator/
COPY services/doc-worker/pyproject.toml services/doc-worker/
COPY services/sandbox/pyproject.toml services/sandbox/
COPY services/workbench/pyproject.toml services/workbench/
RUN uv sync --frozen --no-dev --package jarvis-api --no-install-workspace

COPY packages/common packages/common
COPY services/api services/api
# Shipped instruction layers, shown on the Skills page.
COPY library library
# Absolute, so it resolves whatever the working directory is.
ENV LIBRARY_PATH=/app/library
RUN uv sync --frozen --no-dev --package jarvis-api

RUN useradd --system --uid 10001 jarvis
USER jarvis
WORKDIR /app/services/api
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "jarvis_api.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
