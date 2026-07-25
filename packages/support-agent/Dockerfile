# syntax=docker/dockerfile:1
#
# Image of the `agent-api` block: the brain plus its HTTP door.
# Deployment step 2 — see docs/plan-deploiement-2026-07-25.md.
#
# ⚠️ Build from the REPO ROOT, not from this directory: the build needs the
# workspace root (`uv.lock` + root `pyproject.toml`) to install a reproducible
# slice. The Dockerfile lives next to the package it builds, the context does not.
#
#   docker build -f packages/support-agent/Dockerfile -t support-agent:dev .
#   (or simply `make docker-build`)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — build the virtualenv
# ─────────────────────────────────────────────────────────────────────────────
# Same base image in BOTH stages, on purpose. A venv hard-codes the path of the
# interpreter it was built against, so building against uv's managed Python and
# then running on a different base would produce a venv pointing at an
# interpreter that does not exist in the final image.
FROM python:3.12-slim-bookworm AS build

# uv comes from its own published image: no pip install, version pinned by tag.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# --- Layer 1: third-party dependencies ONLY ---------------------------------
# This is what the uv workspace buys us (plan doc §4). `--no-install-workspace`
# installs langchain/langgraph/fastapi WITHOUT our own code, so editing a graph
# node cannot invalidate this layer — the slow part is cached and rebuilds take
# seconds. Only `uv.lock` changing rebuilds it.
#
# `--frozen`: install exactly what the lockfile says, resolving nothing. The
# image therefore contains what was tested on the developer's machine — not
# "whatever was newest at build time". (`--locked` would additionally verify the
# lockfile, which cannot work here: `packages/client` is excluded from the
# context, so uv would see a workspace member missing.)
# `--no-dev`: no pytest, no ruff in a production image.
# `--extra server`: fastapi + uvicorn, the optional HTTP layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/support-agent/pyproject.toml,target=packages/support-agent/pyproject.toml \
    uv sync --frozen --no-dev --no-install-workspace --package support-agent --extra server

# --- Layer 2: our own code --------------------------------------------------
# `--no-editable` builds a real wheel into the venv instead of linking to the
# source tree. That is what lets the runtime stage copy the venv ALONE: no source
# directory, no risk of shipping a stale `.pyc` next to an edited `.py`.
#
# The root manifests must be COPIED here, not bind-mounted: a `--mount=type=bind`
# exists only for the duration of its own RUN, so layer 1's mounts are already
# gone. Without them uv finds no workspace at all ("No `pyproject.toml` found").
COPY pyproject.toml uv.lock ./
COPY packages/support-agent packages/support-agent
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package support-agent --extra server

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime
# ─────────────────────────────────────────────────────────────────────────────
# Neither uv, nor the build cache, nor the toolchain ships here: a smaller image
# means a faster pull on Azure and a smaller attack surface.
FROM python:3.12-slim-bookworm AS runtime

# PYTHONUNBUFFERED is not cosmetic: without it Python buffers stdout when it is
# not a terminal, so container logs arrive in bursts — or never, if the process
# is killed. Debugging a container whose logs lag is miserable.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# ABSOLUTE paths. On the host these settings are cwd-relative (`./data/kb-velmo`),
# which only works because `make run` is launched from the repo root. A container
# has no such convention, so we pin them — and this is exactly the fragility the
# plan doc flagged (§3). `env_file` in compose can still override these, so
# compose re-declares them; see the comment there.
ENV KNOWLEDGE_DIR=/app/data/kb-velmo \
    PERSISTENCE_BACKEND=sqlite \
    WORKING_MEMORY_DB_PATH=/app/database/working_memory/checkpoints.db \
    AGENT_MEMORY_DB_PATH=/app/database/agent_memory/memories.db

# Non-root: if the process is compromised, it must not own the filesystem it runs
# on. A fixed uid (not just a name) keeps volume ownership predictable.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY --from=build --chown=appuser:appuser /app/.venv /app/.venv
# The FAQ is versioned SOURCE (written by a human), so it belongs IN the image —
# unlike `database/`, which is runtime state and belongs to a volume.
COPY --chown=appuser:appuser data/kb-velmo /app/data/kb-velmo

# Created here, owned by appuser, so a fresh named volume mounted over it
# inherits that ownership — otherwise Docker creates it root-owned and the
# non-root process cannot write its checkpoints.
RUN mkdir -p /app/database/working_memory /app/database/agent_memory \
    && chown -R appuser:appuser /app/database

USER appuser

EXPOSE 8000

# `start-period` covers the warm-up (the FAQ index, ~3 s measured), so a slow
# start is not mistaken for a failure. Probing with urllib rather than curl keeps
# the image free of an extra package.
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# `--host 0.0.0.0` is mandatory, and it is THE classic first-container trap:
# uvicorn defaults to 127.0.0.1, which inside a container means "reachable from
# this container only" — the published port would answer nothing.
CMD ["uvicorn", "support_agent.server:app", "--host", "0.0.0.0", "--port", "8000"]
