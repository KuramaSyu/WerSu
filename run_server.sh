#!/bin/bash
set -a
source .env.default
uv run -- python -m src.main
set +a