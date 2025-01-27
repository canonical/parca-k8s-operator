#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
from subprocess import getstatusoutput

import pytest
from helpers import get_unit_fqdn

from nginx import CA_CERT_PATH, NGINX_PORT

PARCA = "parca"
PARCA_TESTER = "parca-tester"
SSC = "self-signed-certificates"
# Path where SSC saves the CA certificate
SSC_CA_CERT_PATH = "/tmp/ca-cert.pem"


def query_parca_server(model_name, exec_target_app_name, ca_cert_path=CA_CERT_PATH, url_path=""):
    """Run a query the parca server using TLS."""
    parca_address = get_unit_fqdn(model_name, PARCA, 0)
    url = f"https://{parca_address}:{NGINX_PORT}{url_path}"
    # Parca's certificate only contains the fqdn address of parca as SANs.
    # To query the parca server with TLS while validating the certificate, we need to perform the query
    # against the parca server's fqdn.
    # We can do that from inside another K8s pod, such as ssc.
    cmd = f"""juju exec --model {model_name} --unit {exec_target_app_name}/0 "curl --cacert {ca_cert_path} {url}" """
    print(cmd)
    return getstatusoutput(cmd)


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


async def test_direct_url_200(ops_test):
    exit_code, output = query_parca_server(ops_test.model_name, SSC, ca_cert_path=SSC_CA_CERT_PATH)
    assert exit_code == 0, f"Failed to query the parca server. {output}"


@pytest.mark.abort_on_fail
async def test_deploy_parca_tester(ops_test, parca_charm, parca_resources):
    # Deploy and integrate tester charm
    await ops_test.model.deploy(
        parca_charm,
        resources=parca_resources,
        application_name=PARCA_TESTER,
        trust=True,
    )
    await asyncio.gather(
        ops_test.model.integrate(PARCA, f"{PARCA_TESTER}:self-profiling-endpoint"),
        ops_test.model.integrate(f"{PARCA_TESTER}:certificates", SSC),
    )
    await ops_test.model.wait_for_idle(apps=[PARCA, PARCA_TESTER], status="active", timeout=500)


async def test_tls_scraping(ops_test):
    exit_code, output = query_parca_server(ops_test.model_name, PARCA_TESTER, url_path="/metrics")
    assert exit_code == 0, f"Failed to query the parca server. {output}"
    assert PARCA_TESTER in output


@pytest.mark.abort_on_fail
@pytest.mark.teardown
async def test_remove_tls(ops_test):
    # FIXME: should we be disintegrating the tester-ssc relation too?
    await ops_test.juju("remove-relation", PARCA + ":certificates", SSC + ":certificates")
    # we need to wait for a while until parca's nginx loses the TLS connection
    await ops_test.model.wait_for_idle(apps=[PARCA], status="active", timeout=500, idle_period=60)


async def test_direct_url_400(ops_test):
    exit_code, _ = query_parca_server(ops_test.model_name, SSC, SSC_CA_CERT_PATH)
    assert exit_code != 0


@pytest.mark.teardown
async def test_remove_parca(ops_test):
    await ops_test.model.remove_application(PARCA)
