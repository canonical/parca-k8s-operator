#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import json

import pytest
import requests
import sh

TRAEFIK = "traefik-k8s"
PARCA = "parca-k8s"


@pytest.mark.abort_on_fail
async def test_ingress_traefik_k8s(ops_test, parca_charm, parca_oci_image):
    """Test that Parca can be related with Traefik for ingress."""
    apps = [PARCA, TRAEFIK]

    await asyncio.gather(
        ops_test.model.deploy(
            parca_charm,
            resources={"parca-image": parca_oci_image},
            application_name=PARCA,
        ),
        ops_test.model.deploy(
            TRAEFIK,
            application_name=TRAEFIK,
            channel="edge",
            config={"routing_mode": "subdomain", "external_hostname": "foo.bar"},
            trust=True,
        ),
        ops_test.model.wait_for_idle(apps=apps, status="active", timeout=1000),
    )

    # Create the relation
    await ops_test.model.integrate(f"{PARCA}:ingress", TRAEFIK)
    # Wait for the two apps to quiesce
    await ops_test.model.wait_for_idle(apps=apps, status="active", timeout=1000)

    result = await _retrieve_proxied_endpoints(ops_test, TRAEFIK)
    assert result.get(PARCA, None) == {"url": f"http://{ops_test.model_name}-{PARCA}.foo.bar/"}


async def test_ingress_functions_correctly(ops_test):
    result = sh.kubectl(
        *f"-n {ops_test.model.name} get svc/{TRAEFIK}-lb -o=jsonpath='{{.status.loadBalancer.ingress[0].ip}}'".split()
    )
    ip_address = result.strip("'")

    r = requests.get(
        f"http://{ip_address}:80/metrics",
        headers={"Host": f"{ops_test.model_name}-{PARCA}.foo.bar"},
    )
    assert "go_build_info" in r.text
    assert r.status_code == 200


async def _retrieve_proxied_endpoints(ops_test, traefik_application_name):
    traefik_application = ops_test.model.applications[traefik_application_name]
    traefik_first_unit = next(iter(traefik_application.units))
    action = await traefik_first_unit.run_action("show-proxied-endpoints")
    await action.wait()
    result = await ops_test.model.get_action_output(action.id)

    return json.loads(result["proxied-endpoints"])
