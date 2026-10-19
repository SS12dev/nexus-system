# Build from the repository root: docker build -f services/orchestrator/Dockerfile .
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
RUN uv sync --frozen --no-dev --package jarvis-orchestrator --no-install-workspace

COPY packages/common packages/common
COPY services/orchestrator services/orchestrator
RUN uv sync --frozen --no-dev --package jarvis-orchestrator

RUN useradd --system --uid 10001 jarvis
USER jarvis
CMD ["arq", "jarvis_orchestrator.worker.WorkerSettings"]
