# ---- CSS ----
FROM node:22-alpine AS css
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tailwind.config.js ./
COPY templates/ templates/
COPY static/tw.css static/tw.css
RUN npm run build:css

# ---- Python deps ----
FROM astral/uv:python3.12-alpine AS python-deps
WORKDIR /app
COPY uv.lock pyproject.toml ./
RUN uv sync --frozen --no-cache --no-dev
COPY app/util/fetch_js.py app/util/fetch_js.py
RUN mkdir -p static && (/app/.venv/bin/python app/util/fetch_js.py || true)

# ---- Final ----
FROM python:3.12-alpine AS final
WORKDIR /app

COPY --from=css /app/static/globals.css static/globals.css
COPY --from=python-deps /app/.venv /app/.venv
COPY --from=python-deps /app/static static/

COPY static/ static/
COPY alembic/ alembic/
COPY alembic.ini alembic.ini
COPY templates/ templates/
COPY app/ app/
COPY CHANGELOG.md CHANGELOG.md

ENV ABR_APP__PORT=8000
ARG VERSION
ENV ABR_APP__VERSION=$VERSION

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD wget -q --spider http://localhost:8000/api/health || exit 1

CMD /app/.venv/bin/fastapi run --port $ABR_APP__PORT
