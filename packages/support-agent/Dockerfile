# syntax=docker/dockerfile:1
#
# Image of the `agent-api` block: the brain plus its HTTP door.
#
# Build from the REPO ROOT, not from this directory: the build needs the
# workspace root (`uv.lock` + root `pyproject.toml`).
#
#   docker build -f packages/support-agent/Dockerfile -t support-agent:dev .
#   (or simply `make docker-build`)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — build the virtualenv
# ─────────────────────────────────────────────────────────────────────────────
# Same base image in BOTH stages: a venv hard-codes the path of the interpreter
# it was built against.
FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# --- Layer 1: third-party dependencies ONLY ---------------------------------
# `--no-install-workspace` keeps our own code out, so editing a graph node
# cannot invalidate this layer. Only `uv.lock` changing rebuilds it.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/support-agent/pyproject.toml,target=packages/support-agent/pyproject.toml \
    uv sync --frozen --no-dev --no-install-workspace --package support-agent --extra server

# --- Layer 2: our own code --------------------------------------------------
# `--no-editable` builds a real wheel into the venv, which is what lets the
# runtime stage copy the venv alone. The root manifests must be COPIED, not
# bind-mounted: layer 1's mounts are already gone.
COPY pyproject.toml uv.lock ./
COPY packages/support-agent packages/support-agent
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package support-agent --extra server

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# Without PYTHONUNBUFFERED, container logs arrive in bursts — or never, if the
# process is killed.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Absolute paths: on the host these settings are cwd-relative. `env_file` in
# compose can still override them, so compose re-declares them.
ENV KNOWLEDGE_DIR=/app/data/kb-velmo \
    PERSISTENCE_BACKEND=sqlite \
    WORKING_MEMORY_DB_PATH=/app/database/working_memory/checkpoints.db \
    AGENT_MEMORY_DB_PATH=/app/database/agent_memory/memories.db

# Non-root, with a fixed uid so volume ownership stays predictable.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY --from=build --chown=appuser:appuser /app/.venv /app/.venv
# The FAQ is versioned source, so it belongs in the image — unlike `database/`,
# which is runtime state and belongs to a volume.
COPY --chown=appuser:appuser data/kb-velmo /app/data/kb-velmo

# Created here and owned by appuser, so a fresh named volume mounted over it
# inherits that ownership instead of being root-owned.
RUN mkdir -p /app/database/working_memory /app/database/agent_memory \
    && chown -R appuser:appuser /app/database

USER appuser

EXPOSE 8000

# `start-period` covers the warm-up (the FAQ index). Probing with urllib rather
# than curl keeps the image free of an extra package.
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# `--host 0.0.0.0` is mandatory: uvicorn defaults to 127.0.0.1, which inside a
# container means the published port would answer nothing.
CMD ["uvicorn", "support_agent.server:app", "--host", "0.0.0.0", "--port", "8000"]
