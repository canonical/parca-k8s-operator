#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio

import pytest

PARCA = "parca"
PARCA_TESTER = "parca-tester"
SSC = "self-signed-certificates"


@pytest.mark.abort_on_fail
@pytest.mark.setup
async def test_setup(ops_test, parca_charm, parca_resources):
    """Deploy parca with s3 and TLS integrations."""
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
