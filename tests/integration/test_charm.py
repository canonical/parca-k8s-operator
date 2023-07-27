#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio

import requests
from pytest import mark
from pytest_operator.plugin import OpsTest
from tenacity import retry
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential as wexp

PARCA = "parca"


@mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, parca_charm, parca_oci_image):
    await asyncio.gather(
        ops_test.model.deploy(
            await parca_charm,
            resources={"parca-image": await parca_oci_image},
            application_name=PARCA,
        ),
        ops_test.model.wait_for_idle(
            apps=[PARCA], status="active", raise_on_blocked=True, timeout=1000
        ),
    )


@mark.abort_on_fail
@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
async def test_application_is_up(ops_test: OpsTest):
    status = await ops_test.model.get_status()  # noqa: F821
    address = status["applications"][PARCA]["public-address"]
    response = requests.get(f"http://{address}:7070/")
    assert response.status_code == 200
    response = requests.get(f"http://{address}:7070/metrics")
    assert response.status_code == 200


# @mark.abort_on_fail
# async def test_profiling_endpoint_relation(ops_test: OpsTest):
#     await asyncio.gather(
#         # Test charm to ensure that the relation works properly on Kubernetes
#         ops_test.model.deploy("zinc-k8s", channel="edge", application_name="zinc-k8s"),
#         ops_test.model.wait_for_idle(
#             apps=["zinc-k8s"], status="active", raise_on_blocked=True, timeout=1000
#         ),
#     )

#     await asyncio.gather(
#         ops_test.model.integrate(PARCA, "zinc-k8s"),
#         ops_test.model.wait_for_idle(
#             apps=[PARCA],
#             status="active",
#             raise_on_blocked=True,
#             timeout=1000,
#         ),
#     )


# @mark.abort_on_fail
# @retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
# async def test_profiling_relation_is_configured(ops_test: OpsTest):
#     status = await ops_test.model.get_status()  # noqa: F821
#     address = status["applications"][PARCA]["public-address"]
#     response = requests.get(f"http://{address}:7070/metrics")
#     assert "zinc" in response.text


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
