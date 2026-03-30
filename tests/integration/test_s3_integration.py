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


@pytest.mark.juju_setup
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
        lambda status: jubilant.all_active(status, PARCA, S3_APP, PARCA_TESTER),
        error=jubilant.any_error,
        delay=5,
        successes=3,
        timeout=2000,
    )


def get_s3_connection_info(juju: Juju) -> dict:
    """Get connection info from s3 relation.

    We need to get the endpoint from juju to be able to connect to minio from the
    runner (outside of the k8s cluster).
    """
    result = juju.cli("show-unit", f"{PARCA}/0", "--format=json")
    data = json.loads(result)
    unit_data = data[f"{PARCA}/0"]
    relation_info = unit_data.get("relation-info", [])

    s3_info = {}
    for rel in relation_info:
        if rel.get("endpoint") == "s3":
            app_data = rel.get("application-data", {})
            s3_info = {
                "access_key": app_data.get("access-key"),
                "secret_key": app_data.get("secret-key"),
                "bucket": app_data.get("bucket"),
                "endpoint": app_data.get("endpoint"),
            }
            break

    # endpoint is a cluster address, we need to convert it to a node address
    s3_info["endpoint"] = f"http://{get_unit_ip_address(juju, S3_APP, 0)}:8333"
    return s3_info


def restart_parca_to_flush(model_name: str):
    """Restart Parca to force flush profiles to S3."""
    logger.info("restarting parca to force profile flush to S3...")
    check_call(
        shlex.split(
            f"juju exec --model {model_name} --unit {PARCA}/0 -- pkill -f parca"
        )
    )


def wait_for_parca_ready(model_name: str, timeout: int = 300) -> None:
    """Wait for Parca to be ready again after restart."""
    check_call(
        shlex.split(
            f"juju wait-for unit {PARCA}/0 --model {model_name} --timeout={timeout}s"
        )
    )


@retry(wait=wexp(multiplier=1, min=4, max=10), stop=stop_after_delay(300))
def verify_objects_in_s3(juju: Juju, s3_info: dict, expected_prefix: str) -> None:
    """Verify objects exist in S3 with the expected prefix."""
    logger.info("attempting to list objects in minio bucket")
    minio_client = Minio(
        re.sub(r"^https?://", "", s3_info["endpoint"]),  # minio client doesn't like the scheme
        access_key=s3_info["access_key"],
        secret_key=s3_info["secret_key"],
        secure=False,
    )
    bucket_name = s3_info["bucket"]
    objects = list(minio_client.list_objects(bucket_name, recursive=True))

    if not objects:
        raise AssertionError(f"No objects found in bucket {bucket_name}")

    has_prefix = any(obj.object_name.startswith(expected_prefix) for obj in objects)
    if not has_prefix:
        raise AssertionError(
            f"No objects with prefix '{expected_prefix}' found. "
            f"Objects: {[obj.object_name for obj in objects]}"
        )
    logger.info(
        f"Found {len(objects)} objects in bucket, including expected prefix '{expected_prefix}'"
    )


def test_parca_data_ingestion(juju: Juju, parca_charm, parca_resources):
    """Verify Parca receives and stores data in S3.

    Steps:
    1. Get S3 connection info from the relation
    2. Restart Parca to force flushing profiles to S3
    3. Wait for Parca to come back up
    4. Check S3 for objects with expected prefix
    """
    # Step 1: Get S3 connection info
    s3_info = get_s3_connection_info(juju)
    assert all(s3_info.values()), f"Missing S3 connection info: {s3_info}"
    logger.info(f"S3 connection info: {s3_info}")

    # Step 2: Restart parca to trigger flush
    restart_parca_to_flush(juju.model)

    # Step 3: Wait for parca to be ready after restart
    wait_for_parca_ready(juju.model)

    # Step 4: Retry checking object storage for objects
    verify_objects_in_s3(juju, s3_info, EXPECTED_OBJECT_PREFIX)


@pytest.mark.juju_teardown
def test_teardown(juju: Juju):
    juju.remove_relation(f"{PARCA}:s3", S3_APP)

    juju.wait(lambda status: jubilant.all_active(status, PARCA), timeout=1000)

    juju.remove_application(PARCA)
