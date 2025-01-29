#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio

import requests
from helpers import deploy_tempo_cluster, get_traces, get_unit_ip, query_parca_server, get_pubic_address
from pytest import mark
from pytest_operator.plugin import OpsTest
from tenacity import retry
from tenacity.stop import stop_after_attempt, stop_after_delay
from tenacity.wait import wait_exponential as wexp

PARCA = "parca"


@mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, parca_charm, parca_resources):
    await asyncio.gather(
        ops_test.model.deploy(
            parca_charm,
            resources=parca_resources,
            application_name=PARCA,
        ),
        ops_test.model.wait_for_idle(
            apps=[PARCA], status="active", raise_on_blocked=True, timeout=1000
        ),
    )


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
async def test_application_is_up(ops_test: OpsTest):
    address = await get_pubic_address(ops_test, PARCA)
    response = requests.get(f"http://{address}:8080/")
    assert response.status_code == 200
    response = requests.get(f"http://{address}:8080/metrics")
    assert response.status_code == 200


@mark.abort_on_fail
async def test_profiling_endpoint_relation(ops_test: OpsTest):
    await asyncio.gather(
        # Test charm to ensure that the relation works properly on Kubernetes
        ops_test.model.deploy("zinc-k8s", channel="edge", application_name="zinc-k8s"),
        ops_test.model.wait_for_idle(
            apps=["zinc-k8s"], status="active", raise_on_blocked=True, timeout=1000
        ),
    )

    await asyncio.gather(
        ops_test.model.integrate(PARCA, "zinc-k8s"),
        ops_test.model.wait_for_idle(
            apps=[PARCA],
            status="active",
            raise_on_blocked=True,
            timeout=1000,
        ),
    )


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
async def test_profiling_relation_is_configured(ops_test: OpsTest):
    address = await get_pubic_address(ops_test, PARCA)
    response = requests.get(f"http://{address}:8080/metrics")
    assert "zinc" in response.text


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
async def test_self_profiling(ops_test: OpsTest):
    address = await get_pubic_address(ops_test, PARCA)
    response = requests.get(f"http://{address}:8080/metrics")
    assert f'"{PARCA}"' in response.text


@mark.abort_on_fail
async def test_metrics_endpoint_relation(ops_test: OpsTest):
    await asyncio.gather(
        ops_test.model.deploy(
            "prometheus-k8s",
            channel="edge",
            trust=True,
            application_name="prometheus",
        ),
        ops_test.model.wait_for_idle(
            apps=["prometheus"], status="active", raise_on_blocked=True, timeout=1000
        ),
    )

    await asyncio.gather(
        ops_test.model.integrate(f"{PARCA}:metrics-endpoint", "prometheus"),
        ops_test.model.wait_for_idle(
            apps=[PARCA, "prometheus"],
            status="active",
            raise_on_blocked=True,
            timeout=1000,
        ),
    )


@mark.abort_on_fail
async def test_grafana_dashboard_relation(ops_test: OpsTest):
    await asyncio.gather(
        ops_test.model.deploy(
            "grafana-k8s",
            channel="edge",
            trust=True,
            application_name="grafana",
        ),
        ops_test.model.wait_for_idle(
            apps=["grafana"], status="active", raise_on_blocked=True, timeout=1000
        ),
    )

    await asyncio.gather(
        ops_test.model.integrate(f"{PARCA}:grafana-dashboard", "grafana"),
        ops_test.model.wait_for_idle(
            apps=[PARCA, "grafana"],
            status="active",
            raise_on_blocked=True,
            timeout=1000,
        ),
    )


async def test_workload_tracing_relation(ops_test: OpsTest):
    await deploy_tempo_cluster(ops_test)
    await asyncio.gather(
        ops_test.model.integrate(f"{PARCA}:workload-tracing", "tempo"),
        ops_test.model.wait_for_idle(
            apps=[PARCA, "tempo", "tempo-worker"],
            status="active",
            raise_on_blocked=True,
            timeout=500,
        ),
    )

    # Stimulate parca to generate traces
    exit_code, output = query_parca_server(
        ops_test.model_name,
        "tempo",
    )
    assert exit_code == 0, f"Failed to query the parca server. {output}"

    # Verify workload traces from parca are ingested into Tempo
    assert await get_traces(
        await get_unit_ip(ops_test.model_name, "tempo", 0),
        service_name=PARCA,
        tls=False,
    )
