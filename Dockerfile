FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY . .
CMD ["uv", "run", "--no-sync", "uvicorn", "web.app:app", "--host", "127.0.0.1", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
