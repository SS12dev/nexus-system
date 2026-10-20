# Build from the repository root: docker build -f services/workbench/Dockerfile .
# Where dev teams build software: one persistent git folder per project, with Node 22 and npm,
# Python 3.13 and uv, git and headless Chromium for Playwright tests. Internet only through the
# egress-proxy allowlist (Compose puts it on an internal network), a read-only root, and
# resource limits per command.
FROM node:22-bookworm-slim AS node

FROM python:3.13-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /usr/local/bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends git curl procps \
    && rm -rf /var/lib/apt/lists/*

# Playwright and the Chromium it drives, with Chromium's system libraries. Projects should add
# the same version (@playwright/test@$PLAYWRIGHT_VERSION) so it finds this browser.
ARG PLAYWRIGHT_VERSION=1.64.1
ENV PLAYWRIGHT_VERSION=${PLAYWRIGHT_VERSION}
RUN npm install -g --no-fund --no-audit @playwright/test@${PLAYWRIGHT_VERSION} \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.npm /root/.cache

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/
COPY services/api/pyproject.toml services/api/
COPY services/orchestrator/pyproject.toml services/orchestrator/
COPY services/doc-worker/pyproject.toml services/doc-worker/
COPY services/sandbox/pyproject.toml services/sandbox/
COPY services/workbench/pyproject.toml services/workbench/
RUN uv sync --frozen --no-dev --package jarvis-workbench --no-install-workspace

COPY services/workbench services/workbench
RUN uv sync --frozen --no-dev --package jarvis-workbench

# Everything reaches the internet through the allowlist proxy; npm and git also get it in their
# own config, for tools that ignore the environment.
ENV HTTP_PROXY=http://egress-proxy:8888 HTTPS_PROXY=http://egress-proxy:8888 \
    http_proxy=http://egress-proxy:8888 https_proxy=http://egress-proxy:8888 \
    NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1
RUN npm config set --global proxy http://egress-proxy:8888 \
    && npm config set --global https-proxy http://egress-proxy:8888 \
    && git config --system http.proxy http://egress-proxy:8888 \
    && git config --system init.defaultBranch main

RUN useradd --system --uid 10003 --home-dir /workspaces workbench \
    && mkdir -p /workspaces && chown workbench /workspaces
USER workbench
EXPOSE 8200
CMD ["uvicorn", "jarvis_workbench.app:app", "--host", "0.0.0.0", "--port", "8200"]
