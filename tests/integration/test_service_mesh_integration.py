#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Integration tests for the Istio service mesh integration."""

import subprocess

import jubilant
import pytest
from helpers import INTEGRATION_TESTERS_CHANNEL, PARCA
from jubilant import Juju
from tenacity import retry
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_exponential as wexp

from nginx import Nginx

ISTIO_K8S = "istio-k8s"
ISTIO_K8S_CHANNEL = "dev/edge"
ISTIO_BEACON = "istio-beacon-k8s"
ISTIO_BEACON_CHANNEL = "dev/edge"
GRAFANA = "graf"
PARCA_TESTER = "parca-tester"



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _exec_python(model: str, app: str, unit: int, code: str) -> subprocess.CompletedProcess:
    """Run a Python3 one-liner inside a unit's charm container via juju exec."""
    return subprocess.run(
        ["juju", "exec", "--model", model, "--unit", f"{app}/{unit}", "--", "python3", "-c", code],
        capture_output=True,
        text=True,
    )


def _assert_tcp_reachable(model: str, from_app: str, from_unit: int, host: str, port: int) -> None:
    """Assert that a TCP port is reachable from inside a unit's container."""
    code = (
        f"import socket; "
        f"socket.create_connection(('{host}', {port}), timeout=10).close(); "
        f"print('ok')"
    )
    proc = _exec_python(model, from_app, from_unit, code)
    assert proc.returncode == 0, (
        f"Port {port} on {host} not reachable from {from_app}/{from_unit}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@pytest.mark.setup
def test_deploy(juju: Juju, parca_charm, parca_resources):
    """Deploy the full mesh topology and wait for all apps to be Active."""
    juju.deploy(ISTIO_K8S, channel=ISTIO_K8S_CHANNEL, trust=True)
    juju.deploy(parca_charm, PARCA, resources=parca_resources, trust=True)
    juju.deploy(parca_charm, PARCA_TESTER, resources=parca_resources, trust=True)
    juju.deploy(ISTIO_BEACON, channel=ISTIO_BEACON_CHANNEL, trust=True)
    juju.deploy("grafana-k8s", GRAFANA, channel=INTEGRATION_TESTERS_CHANNEL, trust=True)


    juju.integrate(f"{PARCA}:grafana-source", GRAFANA)
    juju.integrate(
        f"{PARCA_TESTER}:external-parca-store-endpoint",
        f"{PARCA}:parca-store-endpoint",
    )

    juju.wait(
        lambda status: jubilant.all_active(
            status,
            ISTIO_K8S,
            PARCA,
            PARCA_TESTER,
            ISTIO_BEACON,
            GRAFANA,
        ),
        timeout=1200,
        successes=6,
        delay=10,
    )

@pytest.mark.setup
def test_integrate_with_mesh(juju: Juju):
    juju.integrate(f"{PARCA}:service-mesh", f"{ISTIO_BEACON}:service-mesh")
    juju.integrate(f"{PARCA_TESTER}:service-mesh", f"{ISTIO_BEACON}:service-mesh")
    juju.integrate(f"{GRAFANA}:service-mesh", f"{ISTIO_BEACON}:service-mesh")
    juju.wait(
        lambda status: jubilant.all_active(
            status,
            ISTIO_K8S,
            PARCA,
            PARCA_TESTER,
            ISTIO_BEACON,
            GRAFANA,
        ),
        timeout=1200,
        successes=3,
        delay=10,
    )

# ---------------------------------------------------------------------------
# Policy verification: HTTP port reachable from grafana (grafana-source policy)
# ---------------------------------------------------------------------------


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 5), reraise=True)
def test_http_port_reachable_from_grafana(juju: Juju):
    """Port 7994 (HTTP) must be reachable from inside grafana (grafana-source relation).

    The AppPolicy for the grafana-source relation allows grafana's service account
    to access parca on port 7994.  A successful TCP connect proves the policy is
    in place and nginx is serving.
    """
    parca_host = f"{PARCA}.{juju.model}.svc.cluster.local"
    _assert_tcp_reachable(juju.model, GRAFANA, 0, parca_host, Nginx.parca_http_server_port)


# ---------------------------------------------------------------------------
# Self-profiling: covers gRPC store (7993) via mesh
# ---------------------------------------------------------------------------



@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 5), reraise=True)
def test_grpc_port_reachable_via_self_profiling(juju: Juju):
    """Port 7993 (gRPC store) must be reachable from inside parca/0.

    After the self-profiling relation is added, parca is both the scraper
    (self-profiling-endpoint policy, HTTP 7994) and the store consumer
    (parca-store-endpoint policy, gRPC 7993).  A successful TCP connect from
    inside parca/0 to its own Kubernetes service on port 7993 confirms the
    AppPolicy for the parca-store-endpoint relation is in place and enforced.
    """
    parca_host = f"{PARCA}.{juju.model}.svc.cluster.local"
    _assert_tcp_reachable(juju.model, PARCA, 0, parca_host, Nginx.parca_grpc_server_port)


# ---------------------------------------------------------------------------
# parca-store-endpoint: gRPC store (7993) via mesh
# ---------------------------------------------------------------------------


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 5), reraise=True)
def test_grpc_port_reachable_from_other_parca(juju: Juju):
    """Port 7993 must be reachable from a second Parca instance.

    This validates the AppPolicy generated for the parca-store-endpoint relation:
    traffic from PARCA_TESTER over external-parca-store-endpoint to PARCA's
    parca-store-endpoint must be allowed by the service mesh.
    """
    parca_host = f"{PARCA}.{juju.model}.svc.cluster.local"
    _assert_tcp_reachable(
        juju.model,
        PARCA_TESTER,
        0,
        parca_host,
        Nginx.parca_grpc_server_port,
    )
