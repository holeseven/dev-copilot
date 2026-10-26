# @author lxy
FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/engine/.venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/

RUN groupadd --system --gid 10001 devcopilot \
    && useradd --system --uid 10001 --gid devcopilot --home-dir /app devcopilot \
    && mkdir -p /app/engine /data/dev-copilot \
    && chown -R devcopilot:devcopilot /app /data/dev-copilot

WORKDIR /app/engine

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=devcopilot:devcopilot . .
RUN uv sync --frozen --no-dev \
    && chown -R devcopilot:devcopilot /app/engine/.venv

USER devcopilot

EXPOSE 8000
CMD ["uvicorn", "engine.main:app", "--host", "0.0.0.0", "--port", "8000"]
