"""Shared SpiceDB container helpers.

The pieces in this module are intentionally low-level: they know how to
*boot* a SpiceDB testcontainer and how to *wait until it accepts
writes*, but not how to build any of the application-specific repos on
top of it.

Higher-level fixtures built on top of these helpers live in
:mod:`tests.fixtures.spicedb` (permission repo),
:mod:`tests.fixtures.postgres` (full env with Postgres + SpiceDB), and
:mod:`tests.fixtures.garage` (Garage S3).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from authzed.api.v1 import AsyncClient, WriteSchemaRequest
from grpcutil import insecure_bearer_token_credentials
from testcontainers_spicedb import SpiceDBContainer

# Single source of truth for which SpiceDB image the suite runs against.
SPICEDB_IMAGE = "authzed/spicedb:v1.47.1"

_REPO_ROOT = Path(__file__).resolve().parents[2]
SPICEDB_SCHEMA_PATH = _REPO_ROOT / "src" / "db" / "migrations" / "schema.zed"


def load_spicedb_schema() -> str:
    """Read the canonical SpiceDB schema from the migrations directory."""
    return SPICEDB_SCHEMA_PATH.read_text(encoding="utf-8")


def create_spicedb_client(endpoint: str, secret_key: str) -> AsyncClient:
    """Construct an authzed ``AsyncClient`` for a running SpiceDB testcontainer."""
    return AsyncClient(endpoint, insecure_bearer_token_credentials(secret_key))


async def wait_until_spicedb_ready(client: AsyncClient, schema: str) -> None:
    """Block until SpiceDB accepts a schema write.

    Polls ``WriteSchema`` because it round-trips through the gRPC server
    and hence is a reliable "the daemon is alive" probe.
    """
    attempts = 30
    retry_delay_s = 0.5
    last_error: Exception | None = None

    for _ in range(attempts):
        try:
            await client.WriteSchema(WriteSchemaRequest(schema=schema))
            return
        except Exception as exc:  # pragma: no cover - startup race only
            last_error = exc
            await asyncio.sleep(retry_delay_s)

    raise RuntimeError(
        "SpiceDB container did not become ready in time"
    ) from last_error


__all__ = [
    "SPICEDB_IMAGE",
    "SPICEDB_SCHEMA_PATH",
    "load_spicedb_schema",
    "create_spicedb_client",
    "wait_until_spicedb_ready",
]
