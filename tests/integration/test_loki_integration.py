#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import json
from subprocess import getoutput

import pytest
from tenacity import retry
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_exponential as wexp

from tests.integration.helpers import get_unit_ip

PARCA = "parca"
LOKI = "loki"


@pytest.mark.abort_on_fail
@pytest.mark.setup
async def test_setup(ops_test, parca_charm, parca_resources):
    """Deploy parca alongside loki."""
    await asyncio.gather(
        ops_test.model.deploy(
            parca_charm,
            resources=parca_resources,
            application_name=PARCA,
            trust=True,
        ),
        ops_test.model.deploy(
            "loki-k8s",
            application_name=LOKI,
            trust=True,
        ),
    )
    await asyncio.gather(
        ops_test.model.integrate(PARCA, f"{LOKI}:logging"),
    )
    await ops_test.model.wait_for_idle(
        apps=[PARCA, LOKI], status="active", timeout=500
    )


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def check_logs(ops_test:OpsTest):
    # check that loki has received logs from the 'parca-k8s' charm.
    loki_ip = get_unit_ip(ops_test.model_name, LOKI, 0)
    cmd = f"curl -s {loki_ip}:3100/loki/api/v1/label/charm/values | jq .data"
    charm_label_values = json.loads(getoutput(cmd))
    assert "parca-k8s" in charm_label_values


@pytest.mark.abort_on_fail
@pytest.mark.teardown
async def test_teardown(ops_test):
    await ops_test.juju("remove-relation", PARCA, LOKI)
    await ops_test.model.wait_for_idle(apps=[PARCA], status="active", timeout=500, idle_period=60)
    await ops_test.model.remove_application(PARCA)
