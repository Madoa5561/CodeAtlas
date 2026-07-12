# syntax=docker/dockerfile:1
FROM python:3.12-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY constraints ./constraints
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels -c constraints/py312.txt .

FROM python:3.12-slim-trixie AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CODEATLAS_APPLICATION__ENVIRONMENT=production \
    CODEATLAS_LOGGING__FORMAT=json \
    CODEATLAS_DATABASE__URL=sqlite:////app/data/codeatlas.db

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 codeatlas \
    && useradd --system --uid 10001 --gid codeatlas --home-dir /app codeatlas \
    && mkdir -p /app/data \
    && chown -R codeatlas:codeatlas /app

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

WORKDIR /app
USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["codeatlas-healthcheck"]

CMD ["codeatlas"]
