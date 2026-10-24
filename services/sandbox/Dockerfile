# Build from the repository root: docker build -f services/sandbox/Dockerfile .
# Runs agent code: no network (Compose puts it on an internal network), a read-only root,
# resource limits per job, and the document and 3D modelling libraries skills rely on.
FROM python:3.13.17-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.33 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib XDG_CACHE_HOME=/tmp/cache
# Pango for WeasyPrint (Markdown/HTML to PDF), fonts so documents and charts have real text.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
      fonts-dejavu-core fonts-liberation2 fonts-noto-core fontconfig \
    && rm -rf /var/lib/apt/lists/* && fc-cache -f
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/
COPY services/api/pyproject.toml services/api/
COPY services/orchestrator/pyproject.toml services/orchestrator/
COPY services/doc-worker/pyproject.toml services/doc-worker/
COPY services/sandbox/pyproject.toml services/sandbox/
COPY services/workbench/pyproject.toml services/workbench/
COPY services/deployer/pyproject.toml services/deployer/
RUN uv sync --frozen --no-dev --package nexus-sandbox --no-install-workspace

COPY services/sandbox services/sandbox
# CAD=false leaves out the 3D modelling libraries (build123d, trimesh: about 600 MB).
ARG CAD=true
RUN uv sync --frozen --no-dev --package nexus-sandbox $([ "$CAD" = true ] && echo --extra cad) \
    && python -c "import matplotlib.pyplot, weasyprint, docx, pptx, openpyxl, pandas, scipy, sklearn, zoneinfo; zoneinfo.ZoneInfo('Asia/Kolkata')" \
    && if [ "$CAD" = true ]; then python -c "import build123d, trimesh, manifold3d, networkx"; fi

RUN useradd --system --uid 10002 sandbox
USER sandbox
EXPOSE 8100
CMD ["uvicorn", "nexus_sandbox.app:app", "--host", "0.0.0.0", "--port", "8100"]
