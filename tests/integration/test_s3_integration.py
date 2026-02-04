#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import shlex
from subprocess import check_call

import jubilant
import minio
import pytest
from jubilant import Juju
from tenacity import retry
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_exponential as wexp

from tests.integration.helpers import BUCKET_NAME, MINIO, S3_INTEGRATOR, deploy_s3, get_unit_ip

PARCA = "parca"
PARCA_TESTER = "parca-tester"
logger = logging.getLogger(__name__)


@pytest.mark.setup
def test_setup(juju:Juju, parca_charm, parca_resources):
    """Deploy parca with s3 and a tester charm (another parca!) to scrape."""
    juju.deploy(
        parca_charm,PARCA,
        resources=parca_resources,
        trust=True,
    )
    juju.deploy(
        parca_charm,PARCA_TESTER,
        resources=parca_resources,
        trust=True,
    )

    deploy_s3(juju, PARCA)

    juju.integrate(PARCA, S3_INTEGRATOR)
    juju.integrate(PARCA, f"{PARCA_TESTER}:self-profiling-endpoint"),

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, PARCA_TESTER, S3_INTEGRATOR, MINIO), timeout=1000
    )


def restart_parca_to_flush(model_name: str):
    """Restart parca once to force flush in-memory buffer to S3."""
    check_call(shlex.split(f"juju ssh -m {model_name} --container parca parca/0 pebble restart parca"))


@retry(wait=wexp(multiplier=1, min=5, max=10), stop=stop_after_delay(60), reraise=True)
def wait_for_parca_ready(model_name: str):
    """Wait for parca to be fully started and responsive after restart."""
    # Check that parca service is active
    check_call(shlex.split(f"juju ssh -m {model_name} --container parca parca/0 pebble services parca"))


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 3), reraise=True)
def verify_objects_in_minio(minio_url: str, expected_obj: str):
    """Verify that expected objects exist in MinIO.

    This function retries checking MinIO without restarting parca on each attempt,
    allowing parca time to flush its in-memory buffer to S3.
    """
    m = minio.Minio(
        endpoint=minio_url, access_key="accesskey", secret_key="secretkey", secure=False
    )
    buckets = m.list_buckets()
    assert buckets, "no bucket created"

    buck = buckets[0]
    assert buck.name == BUCKET_NAME, f"bucket isn't called {BUCKET_NAME!r}"
    objects = list(m.list_objects(BUCKET_NAME))
    assert objects, f"no objects in {BUCKET_NAME!r} bucket"

    assert objects[0].object_name == expected_obj, (
        f"{BUCKET_NAME!r} bucket contains no {expected_obj!r} object"
    )


def test_s3_usage(juju:Juju):
    """Verify that parca is using s3.

    This test:
    1. First checks if parca has already written data to S3 naturally
    2. If no data found, restarts parca once to trigger flush of in-memory buffer
    3. Waits for parca to be ready after restart
    4. Retries checking MinIO for objects
    """
    minio_url = f"{get_unit_ip(juju.model, MINIO, 0)}:9000"
    
    # Step 1: First check if data is already in S3 (parca may write naturally)
    try:
        logger.info("Checking if parca has already written data to S3...")
        verify_objects_in_minio(minio_url, "blocks/")
        logger.info("Data found in S3 without restart!")
        return  # Test passed, no restart needed
    except AssertionError:
        logger.info("No data in S3 yet, will restart parca to trigger flush...")

    # Step 2: Restart parca to trigger flush
    restart_parca_to_flush(juju.model)

    # Step 3: Wait for parca to be ready after restart
    wait_for_parca_ready(juju.model)

    # Step 4: Retry checking MinIO for objects
    verify_objects_in_minio(minio_url, "blocks/")


@pytest.mark.teardown
def test_teardown(juju:Juju):
    juju.remove_relation(PARCA, S3_INTEGRATOR)

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA), timeout=1000
    )

    juju.remove_application(PARCA)
