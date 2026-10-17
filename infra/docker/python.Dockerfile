# The Python services, one target each, built from the repo root:
#   docker build -f infra/docker/python.Dockerfile --target agent --build-arg PACKAGE=agent .
# PACKAGE is the uv workspace member to install; it matches the target.
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

# python:3.12-slim
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build
ARG PACKAGE
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package "$PACKAGE"

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app

FROM runtime AS agent
# Files the wheel does not carry; the embedding model is mounted at FASTEMBED_CACHE_PATH, never baked in.
COPY runbooks /app/runbooks
COPY services/agent/alembic.ini /app/services/agent/alembic.ini
COPY services/agent/migrations /app/services/agent/migrations
ENV FASTEMBED_CACHE_PATH=/models/fastembed
USER app
CMD ["agent"]

FROM runtime AS executor
USER app
CMD ["executor"]

FROM runtime AS sim
COPY evals/scenarios /app/evals/scenarios
USER app
CMD ["sim"]
