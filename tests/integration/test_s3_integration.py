#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

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


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def check_object_in_minio(minio_url, model_name:str, obj_name: str):
    # rely on the fact that parca will force-flush its in-memory buffer when restarted
    check_call(shlex.split(f"juju ssh -m {model_name} --container parca parca/0 pebble restart parca"))

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


def test_s3_usage(juju:Juju):
    """Verify that parca is using s3."""
    minio_url = f"{get_unit_ip(juju.model, MINIO, 0)}:9000"
    check_object_in_minio(minio_url, juju.model, "blocks/")


@pytest.mark.teardown
def test_teardown(juju:Juju):
    juju.remove_relation(PARCA, S3_INTEGRATOR)

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA), timeout=1000
    )

    juju.remove_application(PARCA)
