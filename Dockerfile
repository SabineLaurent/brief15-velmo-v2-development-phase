FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./
COPY src ./src
COPY eval ./eval

RUN uv sync --no-dev --extra vector --extra llm

CMD ["uv", "run", "python", "-m", "velmo.cli"]
