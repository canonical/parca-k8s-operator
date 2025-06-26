#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import jubilant
import pytest
import requests
from helpers import PARCA, get_app_ip_address
from jubilant import Juju
from pytest import mark
from tenacity import retry
from tenacity.stop import stop_after_attempt, stop_after_delay
from tenacity.wait import wait_exponential as wexp

from nginx import Nginx

ZINC = "zinc-k8s"

@mark.setup
def test_deploy(juju: Juju, parca_charm, parca_resources):
    juju.deploy(
        parca_charm,
        PARCA,
        resources=parca_resources,
    )
    juju.wait(
        lambda status: jubilant.all_active(status, PARCA), timeout=1000
    )


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def test_application_is_up(juju: Juju):
    address = get_app_ip_address(juju, PARCA)
    response = requests.get(f"http://{address}:{Nginx.parca_http_server_port}/")
    assert response.status_code == 200
    response = requests.get(f"http://{address}:{Nginx.parca_http_server_port}/metrics")
    assert response.status_code == 200

    with pytest.raises(requests.exceptions.ConnectionError):
        # not a 404, but still nothing we can check without using grpcurl or smth
        requests.get(f"http://{address}:{Nginx.parca_grpc_server_port}/")


@mark.setup
def test_integrate_profiling(juju: Juju):
    # Test charm to ensure that the relation works properly on Kubernetes
    juju.deploy(ZINC, channel="edge")
    juju.integrate(PARCA, "zinc-k8s")

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, ZINC), timeout=1000
    )


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
def test_metrics_profiling(juju: Juju):
    address = get_app_ip_address(juju, PARCA)
    response = requests.get(f"http://{address}:{Nginx.parca_http_server_port}/metrics")
    assert "zinc" in response.text


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
def test_self_profiling(juju: Juju):
    address = get_app_ip_address(juju, PARCA)
    response = requests.get(f"http://{address}:{Nginx.parca_http_server_port}/metrics")
    assert f'"{PARCA}"' in response.text




