#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import json
from subprocess import getoutput

import pytest
import requests
from helpers import get_unit_ip

from nginx import NGINX_PORT

TRAEFIK = "traefik"
PARCA = "parca"


@pytest.mark.abort_on_fail
@pytest.mark.setup
async def test_setup(ops_test, parca_charm, parca_resources):
    """Test that Parca can be related with Traefik for ingress."""
    apps = [PARCA, TRAEFIK]

    await asyncio.gather(
        ops_test.model.deploy(
            parca_charm, resources=parca_resources, application_name=PARCA, base="ubuntu@24.04"
        ),
        ops_test.model.deploy(
            "traefik-k8s",
            application_name=TRAEFIK,
            channel="edge",
            trust=True,
        ),
        ops_test.model.wait_for_idle(apps=apps, status="active", timeout=1000),
    )

    # Create the relation
    await ops_test.model.integrate(f"{PARCA}:ingress", TRAEFIK)
    # Wait for the two apps to quiesce
    await ops_test.model.wait_for_idle(apps=apps, status="active", timeout=1000)


@pytest.fixture
def prefix(ops_test):
    return f"{ops_test.model_name}-{PARCA}"


async def test_proxied_endpoint(ops_test, prefix):
    proxied_endpoints = await _retrieve_proxied_endpoints(ops_test, TRAEFIK)
    ingress_ip = _get_ingress_ip(ops_test.model_name)
    assert proxied_endpoints.get(PARCA, None) == {"url": f"http://{ingress_ip}/{prefix}"}


async def test_ingressed_url_200(ops_test, prefix):
    ingress_ip = _get_ingress_ip(ops_test.model_name)
    url = f"http://{ingress_ip}/{prefix}"
    assert requests.get(url).status_code == 200


async def test_direct_url_200(ops_test, prefix):
    parca_ip = get_unit_ip(ops_test.model_name, PARCA, 0)
    url = f"http://{parca_ip}:{NGINX_PORT}/{prefix}"
    assert requests.get(url).status_code == 200


async def test_direct_url_root_200(ops_test):
    parca_ip = get_unit_ip(ops_test.model_name, PARCA, 0)
    url = f"http://{parca_ip}:{NGINX_PORT}"
    assert requests.get(url).status_code == 200


async def test_direct_url_trailing_slash_200(ops_test, prefix):
    parca_ip = get_unit_ip(ops_test.model_name, PARCA, 0)
    url = f"http://{parca_ip}:{NGINX_PORT}/{prefix}/"
    assert requests.get(url).status_code == 200


def _get_ingress_ip(model_name):
    result = getoutput(
        f"sudo microk8s.kubectl -n {model_name} get svc/{TRAEFIK}-lb -o=jsonpath='{{.status.loadBalancer.ingress[0].ip}}'"
    )
    return result.strip("'")


async def _retrieve_proxied_endpoints(ops_test, traefik_application_name):
    traefik_application = ops_test.model.applications[traefik_application_name]
    traefik_first_unit = next(iter(traefik_application.units))
    action = await traefik_first_unit.run_action("show-proxied-endpoints")
    await action.wait()
    result = await ops_test.model.get_action_output(action.id)

    return json.loads(result["proxied-endpoints"])
