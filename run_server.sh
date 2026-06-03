#!/bin/bash
set -a
source .env
uv run -- python -m src.main
set +a