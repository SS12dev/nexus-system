# Build from the repository root: docker build -f services/deployer/Dockerfile .
# One image, two containers: the deployer (builds and runs apps) and the Docker gate (the only
# one with the Docker socket, allowing a short list of API calls). See compose.yaml.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/
COPY services/api/pyproject.toml services/api/
COPY services/orchestrator/pyproject.toml services/orchestrator/
COPY services/doc-worker/pyproject.toml services/doc-worker/
COPY services/sandbox/pyproject.toml services/sandbox/
COPY services/workbench/pyproject.toml services/workbench/
COPY services/deployer/pyproject.toml services/deployer/
RUN uv sync --frozen --no-dev --package jarvis-deployer --no-install-workspace

COPY services/deployer services/deployer
RUN uv sync --frozen --no-dev --package jarvis-deployer

# The workbench's user, so the deployer can read project folders (mounted read-only).
RUN useradd --system --uid 10003 deployer
USER deployer
EXPOSE 8300
CMD ["uvicorn", "jarvis_deployer.app:app", "--host", "0.0.0.0", "--port", "8300"]
