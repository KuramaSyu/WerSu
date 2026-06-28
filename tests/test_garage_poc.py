"""
POC for a local Garage S3 deployment.

Expected environment file:
    ../infrastructure/.garage.env

Required variables:
    GARAGE_DEFAULT_ACCESS_KEY
    GARAGE_DEFAULT_SECRET_KEY
    GARAGE_DEFAULT_BUCKET

Example:
    pytest tests/test_garage_poc.py -v

These tests validate the most basic S3 workflow:
    1. Upload an object.
    2. Read the object back.
    3. Verify contents.
    4. Remove the object.

The tests assume the Garage S3 endpoint is available at:
    http://localhost:3900
"""

from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from dotenv import dotenv_values

GARAGE_ENV_FILE = Path("infrastructure/.garage.env")
GARAGE_ENDPOINT = "http://localhost:3900"


# dont run this test by default
pytestmark = [pytest.mark.proof_of_concept, pytest.mark.integration]

def load_garage_config() -> dict[str, str]:
    """
    Load Garage credentials from the shared infrastructure env file.

    Raises:
        RuntimeError:
            If the env file is missing or required values are absent.
    """
    if not GARAGE_ENV_FILE.exists():
        raise RuntimeError(
            f"Garage environment file not found: {GARAGE_ENV_FILE}"
        )

    config = dotenv_values(GARAGE_ENV_FILE)

    required = (
        "GARAGE_DEFAULT_ACCESS_KEY",
        "GARAGE_DEFAULT_SECRET_KEY",
        "GARAGE_DEFAULT_BUCKET",
    )

    missing = [key for key in required if not config.get(key)]

    if missing:
        raise RuntimeError(
            f"Missing Garage configuration values: {', '.join(missing)}"
        )

    return {
        "access_key": config["GARAGE_DEFAULT_ACCESS_KEY"],
        "secret_key": config["GARAGE_DEFAULT_SECRET_KEY"],
        "bucket": config["GARAGE_DEFAULT_BUCKET"],
    }


@pytest.fixture(scope="session")
def garage_config() -> dict[str, str]:
    """Provide validated Garage configuration to tests."""
    return load_garage_config()


@pytest.fixture(scope="session")
def s3_client(garage_config):
    """
    Create an S3-compatible client pointed at Garage.

    Garage implements the S3 API, so boto3 can be used directly.
    """
    return boto3.client(
        "s3",
        endpoint_url=GARAGE_ENDPOINT,
        aws_access_key_id=garage_config["access_key"],
        aws_secret_access_key=garage_config["secret_key"],
        region_name="garage",
    )


def test_can_upload_download_and_delete_object(
    s3_client,
    garage_config,
):
    """
    Verify the core object lifecycle works.

    This is intentionally a smoke test rather than a
    integration test. If this passes, credentials, networking,
    bucket access, uploads, downloads, and deletes are all working.
    """
    bucket = garage_config["bucket"]

    object_key = f"pytest/{uuid4()}.txt"
    expected_content = b"hello from garage pytest"

    # Upload a small object.
    s3_client.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=expected_content,
    )

    # Download it again.
    response = s3_client.get_object(
        Bucket=bucket,
        Key=object_key,
    )

    actual_content = response["Body"].read()

    # Ensure round-trip integrity.
    assert actual_content == expected_content

    # Clean up so repeated test runs don't accumulate objects.
    s3_client.delete_object(
        Bucket=bucket,
        Key=object_key,
    )