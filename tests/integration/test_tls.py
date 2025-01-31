#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio

import pytest
from helpers import (
    query_parca_server,
)
from tenacity import retry
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential as wexp

PARCA = "parca"
PARCA_TESTER = "parca-tester"
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


async def test_direct_url_200(ops_test):
    exit_code, output = query_parca_server(
        ops_test.model_name, SSC, tls=True, ca_cert_path=SSC_CA_CERT_PATH
    )
    assert exit_code == 0, f"Failed to query the parca server. {output}"


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
async def test_self_profiling_scraping(ops_test):
    exit_code, output = query_parca_server(
        ops_test.model_name, PARCA, tls=True, url_path="/metrics"
    )
    assert exit_code == 0, f"Failed to query the parca server. {output}"
    assert f'"{PARCA}"' in output


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


async def test_profiling_scraping(ops_test):
    exit_code, output = query_parca_server(
        ops_test.model_name, PARCA_TESTER, tls=True, url_path="/metrics"
    )
    assert exit_code == 0, f"Failed to query the parca server. {output}"
    assert PARCA_TESTER in output


# FIXME: uncomment test once
# https://github.com/canonical/parca-k8s-operator/issues/403
# and https://github.com/canonical/parca-k8s-operator/issues/405 are fixed.
# async def test_workload_tracing(ops_test: OpsTest):
#     await deploy_tempo_cluster(ops_test)
#     await asyncio.gather(
#         ops_test.model.integrate(f"{PARCA}:workload-tracing", "tempo"),
#         ops_test.model.wait_for_idle(
#             apps=[PARCA, "tempo", "tempo-worker"],
#             status="active",
#             raise_on_blocked=True,
#             timeout=500,
#         ),
#     )

#     # Stimulate parca to generate traces
#     exit_code, output = query_parca_server(
#         ops_test.model_name,
#         "tempo",
#         tls=True,
#     )
#     assert exit_code == 0, f"Failed to query the parca server. {output}"

#     # Verify workload traces from parca are ingested into Tempo
#     assert await get_traces(
#         await get_unit_ip(ops_test.model_name, "tempo", 0),
#         service_name=PARCA,
#         tls=True,
#     )


@pytest.mark.abort_on_fail
@pytest.mark.teardown
async def test_remove_tls(ops_test):
    # FIXME: should we be disintegrating the tester-ssc relation too?
    await ops_test.juju("remove-relation", PARCA + ":certificates", SSC + ":certificates")
    # we need to wait for a while until parca's nginx loses the TLS connection
    await ops_test.model.wait_for_idle(apps=[PARCA], status="active", timeout=500)


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
async def test_direct_url_400(ops_test):
    exit_code, _ = query_parca_server(
        ops_test.model_name, SSC, tls=True, ca_cert_path=SSC_CA_CERT_PATH
    )
    assert exit_code != 0


@pytest.mark.teardown
async def test_remove_parca(ops_test):
    await ops_test.model.remove_application(PARCA)
