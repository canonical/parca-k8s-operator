#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.


import jubilant
import pytest
import requests
from jubilant import Juju
from tenacity import retry, stop_after_delay
from tenacity import wait_exponential as wexp

from tests.integration.helpers import get_unit_ip

PARCA = "parca"
LOKI = "loki"


@pytest.mark.setup
async def test_setup(juju:Juju, parca_charm, parca_resources):
    """Deploy parca alongside loki."""
    juju.deploy(
        parca_charm,
        PARCA,
        resources=parca_resources,
        trust=True,
    )
    juju.deploy(
        "loki-k8s",
        LOKI,
        trust=True,
    )
    juju.integrate(PARCA, f"{LOKI}:logging"),
    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, LOKI),
        timeout=500
    )


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def check_logs(juju:Juju):
    # check that loki has received logs from the 'parca-k8s' charm.
    loki_ip = get_unit_ip(juju.model, LOKI, 0)
    charm_labels = requests.get(f"http://{loki_ip}:3100/loki/api/v1/label/charm/values").json()['data']
    assert "parca-k8s" in charm_labels


@pytest.mark.teardown
async def test_teardown(juju:Juju):
    juju.remove_relation(PARCA, LOKI)
    juju.wait(
        lambda status: jubilant.all_active(status, PARCA),
        timeout=500, delay=60
    )
    juju.remove_application(PARCA)

