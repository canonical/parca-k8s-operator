#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import shlex
import subprocess

import pytest
import requests
from tenacity import retry
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_exponential as wexp

from helpers import get_unit_ip
from nginx import Nginx

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


def _get_ingress_ip(model_name):
    cmd = f"microk8s.kubectl -n {model_name} get svc/{TRAEFIK}-lb -o=jsonpath='{{.status.loadBalancer.ingress[0].ip}}'"
    try:
        proc = subprocess.run(shlex.split(cmd), text=True, capture_output=True)
    except:
        proc = subprocess.run(shlex.split("sudo " + cmd), text=True, capture_output=True)
    return proc.stdout.strip("'")


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
@pytest.mark.parametrize("port", (Nginx.parca_http_server_port,
                                  Nginx.parca_grpc_server_port))
async def test_ingressed_endpoints(ops_test, port):
    ingress_ip = _get_ingress_ip(ops_test.model_name)
    url = f"http://{ingress_ip}:{port}"
    # traefik will correctly give 200s on both grpc and http endpoints
    assert requests.get(url).status_code == 200


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
async def test_direct_endpoint_http(ops_test):
    parca_ip = get_unit_ip(ops_test.model_name, PARCA, 0)
    url = f"http://{parca_ip}:{Nginx.parca_http_server_port}"
    assert requests.get(url).status_code == 200


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
async def test_direct_endpoint_grpc(ops_test):
    parca_ip = get_unit_ip(ops_test.model_name, PARCA, 0)
    # when hitting directly parca on the grpc port, requests gives a bad error:
    #  ConnectionError: 'Connection aborted.', BadStatusLine...
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"http://{parca_ip}:{Nginx.parca_grpc_server_port}")
