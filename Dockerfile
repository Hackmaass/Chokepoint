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
EXPOSE 8000
CMD ["uvicorn", "chokepoint.api:app", "--host", "0.0.0.0", "--port", "8000"]
