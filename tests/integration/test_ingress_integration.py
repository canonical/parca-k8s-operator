#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import shlex
import subprocess

import jubilant
import pytest
import requests
from helpers import get_unit_ip
from jubilant import Juju
from tenacity import retry
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_exponential as wexp

from nginx import Nginx

TRAEFIK = "traefik"
PARCA = "parca"


@pytest.mark.setup
def test_setup(juju:Juju, parca_charm, parca_resources):
    """Test that Parca can be related with Traefik for ingress."""
    juju.deploy(
        parca_charm,PARCA, resources=parca_resources, base="ubuntu@24.04"
    )
    juju.deploy(
        "traefik-k8s",
        TRAEFIK,
        channel="edge",
        trust=True,
    )
    juju.integrate(f"{PARCA}:ingress", TRAEFIK)

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, TRAEFIK), timeout=1000
    )


def _get_ingress_url(model: str):
    # use an action instead of running microk8s.kubectl commands as those behave differently in CI
    # this took me a week to figure out. If you ever refactor this do let me know.
    cmd = f"juju run -m {model} {TRAEFIK}/0 show-proxied-endpoints --format json"
    proc = subprocess.run(shlex.split(cmd), text=True, capture_output=True)
    out = json.loads(proc.stdout.strip())
    result = out[f"{TRAEFIK}/0"]["results"]["proxied-endpoints"]
    endpoints = json.loads(result)
    traefik_url = endpoints["traefik"]['url']
    return traefik_url


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
@pytest.mark.parametrize("port", (Nginx.parca_http_server_port,
                                  Nginx.parca_grpc_server_port))
def test_ingressed_endpoints(juju:Juju, port):
    ingress_url = _get_ingress_url(juju.model)
    url = f"{ingress_url}:{port}"
    # traefik will correctly give 200s on both grpc and http endpoints
    assert requests.get(url).status_code == 200


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def test_direct_endpoint_http(juju:Juju):
    parca_ip = get_unit_ip(juju.model, PARCA, 0)
    url = f"http://{parca_ip}:{Nginx.parca_http_server_port}"
    assert requests.get(url).status_code == 200


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def test_direct_endpoint_grpc(juju:Juju):
    parca_ip = get_unit_ip(juju.model, PARCA, 0)
    # when hitting directly parca on the grpc port, requests gives a bad error:
    #  ConnectionError: 'Connection aborted.', BadStatusLine...
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"http://{parca_ip}:{Nginx.parca_grpc_server_port}")
