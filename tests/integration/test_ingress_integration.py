#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
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
from pytest_bdd import given, when, then, scenario

from nginx import Nginx

TRAEFIK = "traefik"
PARCA = "parca"

@scenario('list_endpoints.feature', 'ingressed')
@scenario('ingress.feature', 'curl')
def test_ingress():
    pass


@given("parca-k8s deployment")
def deploy_parca(no_setup, juju:Juju, parca_charm, parca_resources):
    """Deploy parca."""
    if no_setup:
        return

    juju.deploy(
        parca_charm, PARCA, resources=parca_resources
    )

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, TRAEFIK), timeout=1000
    )


@given("an ingress integration")
def integrate_ingress(no_setup, juju:Juju):
    """Test that Parca can be related with Traefik for ingress."""
    if no_setup:
        return

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


@when("The admin runs the `list-endpoints` juju action", target_fixture="action_output")
def run_list_endpoints_action(juju:Juju):
    # WHEN The admin runs the `list-endpoints` juju action
    return juju.run(PARCA+"/0", "list-endpoints")


@then("The admin obtains the direct parca http and grpc server urls")
def check_direct_urls_in_action_output(juju:Juju, action_output):
    # THEN The admin obtains the direct parca http and grpc server urls
    parca_ip = get_unit_ip(juju.model, PARCA, 0)
    assert action_output.results == {
        "direct_http_url": f"http://{parca_ip}:{Nginx.parca_http_server_port}",
        "direct_grpc_url": f"{parca_ip}:{Nginx.parca_grpc_server_port}",
    }


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


def test_list_endpoints_action_includes_ingressed_urls(juju:Juju, port):
    # WHEN The admin runs the `list-endpoints` juju action
    out = juju.run(PARCA+"/0", "list-endpoints")

    # THEN The admin obtains the direct and ingressed parca http and grpc server urls
    parca_ip = get_unit_ip(juju.model, PARCA, 0)
    ingress_url = _get_ingress_url(juju.model)
    assert out.results == {
        "direct_http_url": f"http://{parca_ip}:{Nginx.parca_http_server_port}",
        "direct_grpc_url": f"{parca_ip}:{Nginx.parca_grpc_server_port}",
        "ingressed_http_url": f"{ingress_url}:{Nginx.parca_http_server_port}",
        "ingressed_grpc_url": f"{ingress_url}:{Nginx.parca_grpc_server_port}",
    }


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
