#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Generic COS integration test.

Tests integrations with:
- loki
- prom
- catalog
- grafana
"""
import json

import jubilant
import pytest
import requests
from jubilant import Juju
from tenacity import retry, stop_after_delay
from tenacity import wait_exponential as wexp

from tests.integration.helpers import (
    INTEGRATION_TESTERS_CHANNEL,
    PARCA,
    get_unit_ip,
    get_unit_ip_address,
)

LOKI = "loki"
PROMETHEUS="prometheus"
CATALOGUE="catalogue"
GRAFANA = "graf"


@pytest.fixture(scope="module")
def grafana_admin_creds(juju)->str:
    # NB this fixture can only be accessed after GRAFANA has been deployed.
    # obtain admin credentials via juju action, formatted as "username:password" (for basicauth)
    result = juju.run(GRAFANA+"/0", "get-admin-password")
    return f"admin:{result.results['admin-password']}"


@pytest.mark.setup
def test_setup(juju:Juju, parca_charm, parca_resources):
    """Deploy parca alongside loki."""
    juju.deploy(
        parca_charm,
        PARCA,
        resources=parca_resources,
        trust=True,
    )

    # LOKI
    juju.deploy(
        "loki-k8s",
        LOKI,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )
    juju.integrate(PARCA, f"{LOKI}:logging"),

    # PROM
    juju.deploy(
        "prometheus-k8s",
        PROMETHEUS,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )
    juju.integrate(f"{PARCA}:metrics-endpoint", PROMETHEUS)

    # CATALOG
    juju.deploy(
        "catalogue-k8s",
        CATALOGUE,
        channel=INTEGRATION_TESTERS_CHANNEL,
    )
    juju.integrate(PARCA, CATALOGUE)

    # GRAFANA
    juju.deploy(
        "grafana-k8s",
        GRAFANA,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )
    juju.integrate(f"{PARCA}:grafana-dashboard", GRAFANA)
    juju.integrate(f"{PARCA}:grafana-source", GRAFANA)

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, LOKI, PROMETHEUS, CATALOGUE, GRAFANA), timeout=1000
    )


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def test_metrics_integration(juju:Juju):
    prom_ip = get_unit_ip_address(juju, PROMETHEUS, 0)
    res = requests.get(f"http://{prom_ip}:9090/api/v1/label/juju_application/values")
    assert PARCA in res.json()['data']


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def test_catalogue_integration(juju: Juju):
    # GIVEN a pyroscope cluster integrated with catalogue
    catalogue_unit = f"{CATALOGUE}/0"
    # get Pyroscope's catalogue item URL
    out = juju.cli(
        "show-unit", catalogue_unit, "--endpoint", "catalogue", "--format", "json"
    )
    pyroscope_app_databag = json.loads(out)[catalogue_unit]["relation-info"][0][
        "application-data"
    ]
    url = pyroscope_app_databag["url"]
    # WHEN we query the Pyroscope catalogue item URL
    # query the url from inside the container in case the url is a K8s fqdn
    response = juju.ssh(f"{PARCA}/0", f"curl {url}")
    # THEN we receive a 200 OK response (0 exit status)
    # AND we confirm the response is from the Pyroscope UI (via the page title)
    assert "<title>Grafana Pyroscope</title>" in response


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def test_logging_integration(juju:Juju):
    # check that loki has received logs from the 'parca-k8s' charm.
    loki_ip = get_unit_ip(juju.model, LOKI, 0)
    charm_labels = requests.get(f"http://{loki_ip}:3100/loki/api/v1/label/charm/values").json()['data']
    assert "parca-k8s" in charm_labels


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def test_grafana_source_integration(juju: Juju, grafana_admin_creds):
    """Verify that the parca datasource is registered in grafana."""
    graf_ip = get_unit_ip_address(juju, GRAFANA, 0)
    res = requests.get(f"http://{grafana_admin_creds}@{graf_ip}:3000/api/datasources")
    assert "parca" in {ds['type']for ds in res.json()}


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_delay(60 * 15), reraise=True)
def test_grafana_dashboard_integration(juju: Juju, grafana_admin_creds):
    graf_ip = get_unit_ip_address(juju, GRAFANA, 0)
    # NB: this API is valid for grafana 9.5;
    # once the charm bumps to more recent grafana, this test will break.
    # https://grafana.com/docs/grafana/v9.5/developers/http_api/dashboard/#tags-for-dashboard
    res = requests.get(f"http://{grafana_admin_creds}@{graf_ip}:3000/api/dashboards/tags")
    # sample output:
    # [{"term":"charm: parca-k8s","count":1}]
    assert "charm: parca-k8s" in {dash['term'] for dash in res.json()}


@pytest.mark.teardown
def test_teardown(juju:Juju):
    juju.remove_relation(PARCA, LOKI)
    juju.remove_relation(PARCA+":grafana-dashboard", GRAFANA)
    juju.remove_relation(PARCA+":grafana-source", GRAFANA)
    juju.remove_relation(PARCA, PROMETHEUS)
    juju.remove_relation(PARCA, CATALOGUE)

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA),
        timeout=500, delay=60
    )

    juju.remove_application(PARCA)

