FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.12.13
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY examples ./examples
COPY replays ./replays
COPY scripts ./scripts
COPY --from=web /web/dist ./web/dist
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# Run as an unprivileged user. llm_cache (recorded live runs) and data (the dataset the seed job downloads) are
# the only places the app writes.
RUN useradd --system --uid 10001 --no-create-home app     && mkdir -p /app/llm_cache /app/data && chown app /app/llm_cache /app/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3   CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
# ONE worker on purpose: agent runs and their event streams live in this process's memory (docs/deploy.md).
CMD ["uvicorn", "chokepoint.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
