FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY app ./app

RUN uv sync

RUN mkdir -p /app/data/sessions /app/data/logs /app/data/files

CMD ["uv", "run", "python", "-m", "app.adapters.feishu.long_connection"]
