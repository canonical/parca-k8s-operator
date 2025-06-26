import jubilant
import requests
from jubilant import Juju
from pytest import mark

from tests.integration.helpers import PARCA, get_unit_ip_address

GRAFANA = "grafana"

@mark.setup
def test_setup(juju: Juju, parca_charm, parca_resources):
    juju.deploy(
        parca_charm,
        PARCA,
        resources=parca_resources,
    )
    juju.deploy(
        "grafana-k8s",
        GRAFANA,
        channel="2/edge",
        trust=True,
    )
    juju.integrate(f"{PARCA}:grafana-dashboard", GRAFANA)
    juju.integrate(f"{PARCA}:grafana-source", GRAFANA)

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, GRAFANA), timeout=1000
    )


def test_grafana_source_found(juju: Juju):
    graf_ip = get_unit_ip_address(juju, GRAFANA, 0)
    res = requests.get(f"http://{graf_ip}:3000/api/datasources")
    assert PARCA in {ds['type']for ds in res.json()}


def test_grafana_dashboard_found(juju: Juju):
    raise NotImplementedError()


