# syntax=docker/dockerfile:1
#
# Image of the `client` block: the Chainlit demo shell, and NOTHING else.
# Deployment step 4 — see docs/plan-deploiement-2026-07-25.md.
#
# What is NOT in this image is the interesting part: no LangGraph, no LangChain,
# no `support_agent`, no LLM key, no database driver. The client knows a URL. If
# someone ever pulls this image, they get a chat UI, not the brain.
#
# ⚠️ Build from the REPO ROOT, like the agent's image and for the same reason:
# `uv.lock` + the root `pyproject.toml` live there, and they are what makes the
# install reproducible.
#
#   docker build -f packages/client/Dockerfile -t client-chainlit:dev .
#   (or simply `make docker-up`, which builds both blocks)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — build the virtualenv
# ─────────────────────────────────────────────────────────────────────────────
# Same base image in both stages (a venv hard-codes its interpreter's path), same
# reasoning as packages/support-agent/Dockerfile.
FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Third-party dependencies ONLY (chainlit + httpx + httpx-sse), in their own
# cached layer. `--package client-chainlit` is what keeps the brain out: uv
# installs the closure of THAT member, and since step 4 removed `support-agent`
# from its dependencies, nothing pulls langchain in.
#
# `--frozen`: install exactly what the lockfile says (the same set that was tested
# on the developer's machine), resolving nothing.
# `--no-dev`: no pytest, no ruff in a runtime image.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/client/pyproject.toml,target=packages/client/pyproject.toml \
    uv sync --frozen --no-dev --no-install-workspace --package client-chainlit

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# PYTHONUNBUFFERED: without it Python buffers stdout when it is not a terminal,
# so `docker logs` lags or loses everything on a kill.
# PYTHONPATH=/app: our source is COPIED, not installed as a wheel — see below.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app

# The default target of this client. Compose overrides it with the service name
# (`http://agent-api:8000`); this default is only a sane fallback for a plain
# `docker run` on the host network.
ENV AGENT_API_URL=http://agent-api:8000

RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY --from=build --chown=appuser:appuser /app/.venv /app/.venv

# Our own code is COPIED as source, not installed as a wheel — the opposite of the
# agent's image, and for a concrete reason: `chainlit run` takes a FILE PATH, so
# the app module has to exist as a readable file at a known location. Installing a
# wheel too would put a second copy in site-packages, and two copies of the same
# module is exactly the staleness trap the agent's Dockerfile avoids. One copy,
# reachable both as a path (for the CLI) and as a package (for the import inside
# app.py), thanks to PYTHONPATH above.
COPY --chown=appuser:appuser packages/client/src/client_chainlit /app/client_chainlit

USER appuser

# Chainlit writes into its CURRENT WORKING DIRECTORY — `.files/`, `.chainlit/`,
# `chainlit.md` — and it does so at IMPORT time (`chainlit/config.py` runs
# `FILES_DIRECTORY.mkdir()` while the CLI is still being imported), so no command
# line argument can move it. Under a non-root user in a root-owned `/app`, the
# process therefore dies with `PermissionError: /app/.files` before printing a
# single line of its own.
#
# The fix is not to pre-create each of those paths — that list is Chainlit's to
# change, not ours. We give it a writable cwd instead: the user's home, created by
# `useradd --create-home`. Source stays in `/app` (found via PYTHONPATH),
# scratch state lives here. `CHAINLIT_APP_ROOT` would do the same job explicitly;
# the cwd is what Chainlit reads by default, so we keep the default honest.
WORKDIR /home/appuser

EXPOSE 8000

# Chainlit serves a full page at `/`, and a 200 there means the UI is being
# served. There is no readiness question to ask here: the client holds no state
# and warms nothing up — if the AGENT is not ready, that is the agent's probe to
# fail, not ours. urllib rather than curl keeps the image free of an extra package.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2)"]

# `--host 0.0.0.0` for the same reason as the agent's uvicorn: bound to 127.0.0.1
# inside a container means "reachable from this container only", and the published
# port would answer nothing. No `-w`: hot reload is a dev convenience, and a
# watcher in a runtime image is pure overhead.
CMD ["chainlit", "run", "/app/client_chainlit/app.py", "--host", "0.0.0.0", "--port", "8000"]
