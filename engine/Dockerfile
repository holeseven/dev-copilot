# @author lxy
FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM node:20.19-bookworm-slim AS node-toolchain
FROM python:3.12-slim-bookworm AS runtime

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

FROM runtime AS write-runtime

USER root

COPY --from=node-toolchain /usr/local/bin/node /usr/local/bin/node
COPY --from=node-toolchain /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jdk-headless maven \
    && rm -rf /var/lib/apt/lists/*

USER devcopilot

RUN uv sync --frozen --extra dev

# Keep an untargeted docker build on the production-safe image.
FROM runtime AS default
