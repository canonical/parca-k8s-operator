#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import re
import shlex
from subprocess import check_call, check_output

import jubilant
import pytest
from jubilant import Juju
from minio import Minio
from tenacity import retry
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_exponential as wexp

from tests.integration.helpers import S3_APP, get_unit_ip_address

PARCA = "parca"
PARCA_TESTER = "parca-tester"
EXPECTED_OBJECT_PREFIX = "blocks/"
logger = logging.getLogger(__name__)


@pytest.mark.setup
def test_setup(juju: Juju, parca_charm, parca_resources):
    """Deploy parca with s3 and a tester charm (another parca!) to scrape."""
    juju.deploy(
        parca_charm,
        PARCA,
        resources=parca_resources,
        trust=True,
    )
    juju.deploy(
        parca_charm,
        PARCA_TESTER,
        resources=parca_resources,
        trust=True,
    )

    juju.deploy("seaweedfs-k8s", S3_APP, channel="edge")

    juju.integrate(f"{PARCA}:s3", S3_APP)
    juju.integrate(PARCA, f"{PARCA_TESTER}:self-profiling-endpoint")

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, PARCA_TESTER, S3_APP), timeout=1000, successes=3, delay=10,
    )


def restart_parca_to_flush(model_name: str):
    """Restart parca once to force flush in-memory buffer to S3."""
    check_call(shlex.split(f"juju ssh -m {model_name} --container parca {PARCA}/0 pebble restart parca"))


@retry(wait=wexp(multiplier=1, min=5, max=10), stop=stop_after_delay(60), reraise=True)
def wait_for_parca_ready(model_name: str):
    """Wait for parca to be fully started and responsive after restart."""
    # Check that parca service is active
    check_call(shlex.split(f"juju ssh -m {model_name} --container parca {PARCA}/0 pebble services parca"))


def _get_parca_s3_connection_info(model_name: str) -> dict[str, str]:
    """Read S3 endpoint and credentials from parca's relation data."""
    show_unit_raw = check_output(
        shlex.split(f"juju show-unit -m {model_name} {PARCA}/0 --format json"),
        text=True,
    )
    show_unit = json.loads(show_unit_raw)

    relation_info = show_unit.get(f"{PARCA}/0", {}).get("relation-info", [])
    for relation in relation_info:
        if relation.get("endpoint") != "s3":
            continue
        app_data = relation.get("application-data", {})
        required_fields = ("endpoint", "bucket", "access-key", "secret-key")
        if all(app_data.get(field) for field in required_fields):
            return {
                "endpoint": app_data["endpoint"],
                "bucket": app_data["bucket"],
                "access_key": app_data["access-key"],
                "secret_key": app_data["secret-key"],
            }

    raise AssertionError(f"unable to find S3 credentials in {PARCA}/0 relation data")


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 3), reraise=True)
def verify_objects_in_s3(juju: Juju, s3_info: dict[str, str], expected_prefix: str):
    """Verify that expected objects exist in the S3 backend.

    This function retries checking the S3 API without restarting parca on each attempt,
    allowing parca time to flush its in-memory buffer to object storage.
    """
    bucket_name = s3_info["bucket"]
    unit_ip = get_unit_ip_address(juju, S3_APP, 0)

    # Parse port from s3_info["endpoint"] (format: host:port or http(s)://host:port)
    endpoint_str = s3_info["endpoint"]
    # Remove scheme if present
    endpoint_no_scheme = re.sub(r"^https?://", "", endpoint_str)
    # Extract port
    match = re.match(r"[^:]+:(\d+)", endpoint_no_scheme)
    if not match:
        raise AssertionError(f"Could not parse port from endpoint: {endpoint_str}")
    port = match.group(1)
    endpoint = f"{unit_ip}:{port}"

    client = Minio(
        endpoint=endpoint,
        access_key=s3_info["access_key"],
        secret_key=s3_info["secret_key"],
        secure=False,
    )

    bucket_names = {bucket.name for bucket in client.list_buckets()}
    assert bucket_name in bucket_names, f"bucket {bucket_name!r} was not created"

    objects = list(client.list_objects(bucket_name, recursive=True))
    assert objects, f"no objects in {bucket_name!r} bucket"

    found_prefix = any(obj.object_name.startswith(expected_prefix) for obj in objects)
    assert found_prefix, f"no objects with prefix {expected_prefix!r} in bucket {bucket_name!r}"


def test_s3_usage(juju: Juju):
    """Verify that parca is using S3-backed storage via SeaweedFS.

    This test:
    1. First checks if parca has already written data to S3 naturally
    2. If no data found, restarts parca once to trigger flush of in-memory buffer
    3. Waits for parca to be ready after restart
    4. Retries checking the S3 endpoint for bucket/object presence
    """
    s3_info = _get_parca_s3_connection_info(juju.model)

    # Step 1: First check if data is already in S3 (parca may write naturally)
    try:
        logger.info("Checking if parca has already written data to S3...")
        verify_objects_in_s3(juju, s3_info, EXPECTED_OBJECT_PREFIX)
        logger.info("Data found in S3 without restart!")
        return  # Test passed, no restart needed
    except AssertionError:
        logger.info("No data in S3 yet, will restart parca to trigger flush...")

    # Step 2: Restart parca to trigger flush
    restart_parca_to_flush(juju.model)

    # Step 3: Wait for parca to be ready after restart
    wait_for_parca_ready(juju.model)

    # Step 4: Retry checking object storage for objects
    verify_objects_in_s3(juju, s3_info, EXPECTED_OBJECT_PREFIX)


@pytest.mark.teardown
def test_teardown(juju: Juju):
    juju.remove_relation(f"{PARCA}:s3", S3_APP)

    juju.wait(lambda status: jubilant.all_active(status, PARCA), timeout=1000)

    juju.remove_application(PARCA)
