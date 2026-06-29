"""Garage S3 fixture used by ``test_attachment_integration``.

Previously inlined in the attachment integration test.  Moved here so
that any future S3 integration test (or call site that needs a
disposable S3-compatible store) reuses the same container lifecycle
and the same default-bucket auto-provisioning.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Iterator

import boto3
import pytest
from botocore.config import Config as BotoConfig
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_container_is_ready


GARAGE_IMAGE = "dxflrs/garage:v2.3.0"
GARAGE_S3_PORT = 3900
GARAGE_CONFIG_PATH = "/etc/garage.toml"
GARAGE_HOST_CONFIG = (
    Path(__file__).resolve().parents[2] / "infrastructure" / "garage.toml"
)

TEST_BUCKET = "attachments"
# Deterministic secret. ``garage server --default-bucket`` provisions a key
# with these values automatically when GARAGE_DEFAULT_ACCESS_KEY /
# GARAGE_DEFAULT_SECRET_KEY are set, and grants that key read/write/owner
# permissions on the bucket, so no admin RPC or HTTP round-trip is needed.
TEST_KEY_ID = "GK1a2b3c4d5e6f7g8h9"
TEST_KEY_SECRET = (
    "b21cd517badda12cde455f125d32babd253c2ebefebc48eb91064791fe9e2a9c"
)
# ``infrastructure/garage.toml`` ships with this rpc_secret; forward it so any
# future RPC commands inside the container authenticate.
GARAGE_RPC_SECRET = (
    "181daae763dbfaf1aa5d9f2780f959c890d8ceb21b271e3498028758547e5fa0"
)


def _wait_for_garage(container: DockerContainer) -> None:
    """Block until Garage's S3 port accepts TCP connections."""

    @wait_container_is_ready(AssertionError)
    def _poll() -> None:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(GARAGE_S3_PORT)
        with socket.create_connection((host, port), timeout=2):
            return

    _poll()


@pytest.fixture(scope="session")
def garage_config() -> Iterator[dict[str, str]]:
    """Boot a Garage container and yield endpoint + credentials."""
    container = DockerContainer(GARAGE_IMAGE)
    container.with_volume_mapping(
        host=str(GARAGE_HOST_CONFIG),
        container=GARAGE_CONFIG_PATH,
        mode="ro",
    )
    container.with_env("GARAGE_RPC_SECRET", GARAGE_RPC_SECRET)
    container.with_env("GARAGE_DEFAULT_BUCKET", TEST_BUCKET)
    container.with_env("GARAGE_DEFAULT_ACCESS_KEY", TEST_KEY_ID)
    container.with_env("GARAGE_DEFAULT_SECRET_KEY", TEST_KEY_SECRET)
    container.with_command(["/garage", "server", "--single-node", "--default-bucket"])
    container.with_exposed_ports(GARAGE_S3_PORT)
    container.start()
    try:
        _wait_for_garage(container)
    except Exception:
        container.stop()
        raise

    yield {
        "endpoint": (
            f"http://{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(GARAGE_S3_PORT)}"
        ),
        "access_key": TEST_KEY_ID,
        "secret_key": TEST_KEY_SECRET,
        "bucket": TEST_BUCKET,
    }


@pytest.fixture(scope="session")
def s3_client(garage_config: dict[str, str]):
    """Return a boto3 S3 client wired to the running Garage container."""
    return boto3.client(
        "s3",
        endpoint_url=garage_config["endpoint"],
        aws_access_key_id=garage_config["access_key"],
        aws_secret_access_key=garage_config["secret_key"],
        region_name="garage",
        config=BotoConfig(retries={"max_attempts": 5, "mode": "standard"}),
    )


__all__ = [
    "GARAGE_IMAGE",
    "GARAGE_S3_PORT",
    "GARAGE_CONFIG_PATH",
    "GARAGE_HOST_CONFIG",
    "GARAGE_RPC_SECRET",
    "TEST_BUCKET",
    "TEST_KEY_ID",
    "TEST_KEY_SECRET",
    "garage_config",
    "s3_client",
]
