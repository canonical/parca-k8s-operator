#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import shlex
from subprocess import check_call

import minio
import pytest
from tenacity import retry
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_exponential as wexp

from tests.integration.helpers import BUCKET_NAME, MINIO, S3_INTEGRATOR, deploy_s3, get_unit_ip

PARCA = "parca"
PARCA_TESTER = "parca-tester"


@pytest.mark.abort_on_fail
@pytest.mark.setup
async def test_setup(ops_test, parca_charm, parca_resources):
    """Deploy parca with s3 and a tester charm to scrape."""
    await asyncio.gather(
        ops_test.model.deploy(
            parca_charm,
            resources=parca_resources,
            application_name=PARCA,
            trust=True,
        ),
        ops_test.model.deploy(
            parca_charm,
            resources=parca_resources,
            application_name=PARCA_TESTER,
            trust=True,
        ),
    )
    await deploy_s3(ops_test, PARCA)
    await asyncio.gather(
        ops_test.model.integrate(PARCA, f"{PARCA_TESTER}:self-profiling-endpoint"),
    )
    await ops_test.model.wait_for_idle(
        apps=[PARCA, PARCA_TESTER, S3_INTEGRATOR, MINIO], status="active", timeout=500
    )


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def check_object_in_minio(minio_url, obj_name: str):
    m = minio.Minio(
        endpoint=minio_url, access_key="accesskey", secret_key="secretkey", secure=False
    )
    buckets = m.list_buckets()
    assert buckets, "no bucket created"

    buck = buckets[0]
    assert buck.name == BUCKET_NAME, f"bucket isn't called {BUCKET_NAME!r}"
    objects = list(m.list_objects(BUCKET_NAME))
    assert objects, f"no objects in {BUCKET_NAME!r} bucket"

    assert objects[0].object_name == obj_name, (
        f"{BUCKET_NAME!r} bucket contains no {obj_name!r} object"
    )


async def test_s3_usage(ops_test):
    """Verify that parca is using s3."""
    model_name = ops_test.model.name
    # rely on the fact that parca will force-flush its in-memory buffer when restarted
    check_call(shlex.split(f"juju ssh -m {model_name} --container parca parca/0 pebble restart parca"))
    minio_url = f"{get_unit_ip(model_name, MINIO, 0)}:9000"
    check_object_in_minio(minio_url, "blocks/")


@pytest.mark.abort_on_fail
@pytest.mark.teardown
async def test_teardown(ops_test):
    await ops_test.juju("remove-relation", PARCA, S3_INTEGRATOR)
    await ops_test.model.wait_for_idle(apps=[PARCA], status="active", timeout=500, idle_period=60)
    await ops_test.model.remove_application(PARCA)
