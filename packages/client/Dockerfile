# syntax=docker/dockerfile:1
#
# Image of the `client` block: the Chainlit demo shell, and NOTHING else.
#
# What is NOT in this image is the interesting part: no LangGraph, no LangChain,
# no `support_agent`, no LLM key, no database driver. The client knows a URL.
#
# Build from the REPO ROOT, like the agent's image: `uv.lock` + the root
# `pyproject.toml` live there, and they are what makes the install reproducible.
#
#   docker build -f packages/client/Dockerfile -t client-chainlit:dev .
#   (or simply `make docker-up`, which builds both blocks)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — build the virtualenv
# ─────────────────────────────────────────────────────────────────────────────
# Same base image in both stages: a venv hard-codes its interpreter's path.
FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Third-party dependencies only, in their own cached layer.
# `--package client-chainlit` is what keeps the brain out: uv installs the
# closure of THAT member, which does not depend on `support-agent`.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/client/pyproject.toml,target=packages/client/pyproject.toml \
    uv sync --frozen --no-dev --no-install-workspace --package client-chainlit

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# PYTHONPATH=/app: our source is copied, not installed as a wheel — see below.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app

# Default target; compose overrides it with the service name.
ENV AGENT_API_URL=http://agent-api:8000

RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY --from=build --chown=appuser:appuser /app/.venv /app/.venv

# Copied as source rather than installed as a wheel, unlike the agent's image:
# `chainlit run` takes a FILE PATH, so the app module must exist as a readable
# file. Installing a wheel too would put a second copy in site-packages, and two
# copies of the same module is a staleness trap. One copy, reachable both as a
# path (for the CLI) and as a package (for the import inside app.py).
COPY --chown=appuser:appuser packages/client/src/client_chainlit /app/client_chainlit

USER appuser

# Chainlit writes into its CURRENT WORKING DIRECTORY — `.files/`, `.chainlit/`,
# `chainlit.md` — and it does so at IMPORT time, so no command line argument can
# move it. Under a non-root user in a root-owned `/app` the process dies with
# `PermissionError: /app/.files` before printing a line of its own. So we give it
# a writable cwd: the user's home. Source stays in `/app`, found via PYTHONPATH.
WORKDIR /home/appuser

EXPOSE 8000

# Chainlit serves a full page at `/`, and a 200 there means the UI is being
# served. There is no readiness question to ask: the client holds no state and
# warms nothing up. urllib rather than curl keeps the image free of a package.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2)"]

# `--host 0.0.0.0` for the same reason as the agent's uvicorn. No `-w`: a
# file watcher in a runtime image is pure overhead.
CMD ["chainlit", "run", "/app/client_chainlit/app.py", "--host", "0.0.0.0", "--port", "8000"]
