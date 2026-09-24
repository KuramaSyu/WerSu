# syntax=docker/dockerfile:1.7

# Builder: full toolchain (gcc + curl) to resolve the venv.
# Runtime: just the venv (ONNX Runtime links against glibc
# already in python:slim; no extra system libs required).
# No curl, no uv, no compilers, no libgomp in the final image.
ARG PYTHON_VERSION=3.14

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy lockfiles first so the install layer caches when source changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

COPY src /app/src
COPY logging.yaml /app/logging.yaml

CMD ["python", "-m", "src.main"]
