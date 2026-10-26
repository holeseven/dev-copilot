# @author lxy
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 devcopilot \
    && useradd --system --uid 10001 --gid devcopilot --home-dir /app devcopilot \
    && mkdir -p /app /data/dev-copilot \
    && chown -R devcopilot:devcopilot /app /data/dev-copilot

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY --chown=devcopilot:devcopilot . ./engine

USER devcopilot

EXPOSE 8000
CMD ["uvicorn", "engine.main:app", "--host", "0.0.0.0", "--port", "8000"]
