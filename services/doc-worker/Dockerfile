# Build from the repository root: docker build -f services/doc-worker/Dockerfile .
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app

# OpenCV (used by Docling's OCR) needs these at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/
COPY services/api/pyproject.toml services/api/
COPY services/orchestrator/pyproject.toml services/orchestrator/
COPY services/doc-worker/pyproject.toml services/doc-worker/
COPY services/sandbox/pyproject.toml services/sandbox/
COPY services/workbench/pyproject.toml services/workbench/
RUN uv sync --frozen --no-dev --package jarvis-doc-worker --no-install-workspace

# Docling gives layout-aware PDF tables and OCR for scans and images. It needs PyTorch, taken
# from the CPU wheel index (Docker on a Mac has no GPU), and its models are baked in so parsing
# works offline. Build with --build-arg DOCLING=false for a small image that uses MarkItDown
# for PDFs and can't read images.
ARG DOCLING=true
ARG DOCLING_VERSION=2.140.0
ENV DOCLING_ARTIFACTS_PATH=/opt/docling-models
RUN if [ "$DOCLING" = "true" ]; then \
      uv pip install --python /app/.venv/bin/python "docling==${DOCLING_VERSION}" \
        --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
      && docling-tools models download -o "$DOCLING_ARTIFACTS_PATH"; \
    fi

# Install only our own two packages, leaving the dependencies (and Docling's) as they are.
COPY packages/common packages/common
COPY services/doc-worker services/doc-worker
RUN uv pip install --python /app/.venv/bin/python --no-deps ./packages/common ./services/doc-worker

RUN useradd --system --create-home --uid 10001 jarvis
USER jarvis
CMD ["arq", "jarvis_doc_worker.worker.WorkerSettings"]
