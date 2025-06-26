import jubilant
import requests
from jubilant import Juju
from pytest import mark

from tests.integration.helpers import PARCA, get_unit_ip_address

PROMETHEUS="prometheus"

@mark.abort_on_fail
def test_metrics_endpoint_relation(juju: Juju, parca_charm, parca_resources):
    juju.deploy(
        parca_charm,
        PARCA,
        resources=parca_resources,
    )
    juju.deploy(
        "prometheus-k8s",
        PROMETHEUS,
        channel="2/edge",
        trust=True,
    )
    juju.integrate(f"{PARCA}:metrics-endpoint", PROMETHEUS)

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, PROMETHEUS), timeout=1000
    )


def test_verify_metrics_in_prometheus(juju:Juju):
    prom_ip = get_unit_ip_address(juju, PROMETHEUS, 0)
    res = requests.get(f"http://{prom_ip}:9090/api/v1/label/juju_application/values")
    assert PARCA in res.json()['data']

