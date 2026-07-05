FROM python:3.14-slim

# ENV PYTHONDONTWRITEBYTECODE=1 \
#     PYTHONUNBUFFERED=1 \
#     PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

    
# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Copy dependency files first for Docker layer caching
COPY pyproject.toml uv.lock ./
# Create venv and install dependencies
RUN uv sync

COPY src /app/src
COPY logging.yaml /app/logging.yaml

ENV HUGS_CACHE=/models

CMD ["uv", "run", "--", "python", "-m", "src.main"]
