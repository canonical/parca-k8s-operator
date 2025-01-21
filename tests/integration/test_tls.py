#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
from subprocess import getstatusoutput

import pytest
from helpers import get_unit_fqdn

from nginx import NGINX_PORT

PARCA = "parca"
SSC = "self-signed-certificates"
# Path where SSC saves the CA certificate
SSC_CA_CERT_PATH = "/tmp/ca-cert.pem"


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
    parca_address = get_unit_fqdn(ops_test.model_name, PARCA, 0)
    url = f"https://{parca_address}:{NGINX_PORT}"
    # Parca's certificate only contains the fqdn address of parca as SANs.
    # To query the parca server with TLS while validating the certificate, we need to perform the query
    # against the parca server's fqdn.
    # We can do that from inside another K8s pod, such as ssc.
    cmd = f"""juju exec --model {ops_test.model_name} --unit {SSC}/0 "curl --cacert {SSC_CA_CERT_PATH} {url}" """
    exit_code, output = getstatusoutput(cmd)
    assert exit_code == 0, f"Failed to query {url}. {output}"
