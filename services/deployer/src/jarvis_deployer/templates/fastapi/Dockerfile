# House template: a FastAPI back end on 8000, run with the .venv the team made with uv in the
# workbench (same Python 3.13 on Debian bookworm, so it runs as it is). Built with no network.
FROM python:3.13-slim-bookworm
ARG ENTRY=main:app
WORKDIR /srv/app
COPY src/ ./
RUN test -x .venv/bin/python || { echo "No .venv: run uv sync in the back end's folder." >&2; exit 1; } \
    && useradd --system --uid 10010 app
ENV PATH=/srv/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 ENTRY=${ENTRY}
USER 10010
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn \"$ENTRY\" --host 0.0.0.0 --port 8000"]
