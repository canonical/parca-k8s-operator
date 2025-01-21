#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio

import pytest
import requests
from helpers import get_unit_ip

from nginx import NGINX_PORT

PARCA = "parca"
SSC = "self-signed-certificates"


@pytest.mark.abort_on_fail
@pytest.mark.setup
async def test_setup(ops_test, parca_charm, parca_resources):
    """Test that Parca can be related with Self Signed Certificates for TLS."""
    apps = [PARCA, SSC]

    await asyncio.gather(
        ops_test.model.deploy(
            parca_charm,
            resources=parca_resources,
            application_name=PARCA,
            trust=True,
        ),
        ops_test.model.deploy(
            SSC,
            channel="edge",
            trust=True,
        ),
        ops_test.model.wait_for_idle(apps=apps, status="active", timeout=500),
    )

    # Create the relation
    await ops_test.model.integrate(f"{PARCA}:certificates", SSC)
    # Wait for the two apps to quiesce
    await ops_test.model.wait_for_idle(apps=apps, status="active", timeout=500)


@pytest.mark.abort_on_fail
async def test_direct_url_200(ops_test, ca_cert):
    parca_ip = get_unit_ip(ops_test.model_name, PARCA, 0)
    url = f"https://{parca_ip}:{NGINX_PORT}"
    assert requests.get(url, verify=ca_cert).status_code == 200
