# The `api` service in compose.yaml (grill log Q19): `cmbs serve` with the built dashboard, so the
# page and the API it calls share one origin. Built reproducibly: every base image is pinned by
# digest (the `docker` ecosystem in .github/dependabot.yml bumps them weekly; the node digest must
# equal the web service's in compose.yaml, which the docker-compose ecosystem bumps separately),
# the web bundle from web/package-lock.json with `npm ci`, Python from uv.lock with --locked.
#
#   docker compose up -d --wait api      # then http://localhost:8000/?user=pat

# The dashboard bundle (web/dist), built as the web service builds it.
FROM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# uv, at the version the project's pre-commit hook pins.
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

# Python 3.13 (.python-version), the package and its locked runtime dependencies, no dev tools.
FROM python:3.13-slim-trixie@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1
WORKDIR /app
# Dependencies first, in their own layer, so a code change does not reinstall them.
COPY pyproject.toml uv.lock README.md .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev
COPY --from=web /web/dist ./web/dist
ENV PATH=/app/.venv/bin:$PATH \
    CMBS_WEB_DIST=/app/web/dist
RUN useradd --system --no-create-home cmbs
USER cmbs
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]
CMD ["cmbs", "serve", "--host", "0.0.0.0", "--port", "8000"]
