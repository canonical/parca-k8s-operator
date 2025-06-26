
import jubilant
import pytest
from jubilant import Juju

from tests.integration.helpers import (
    PARCA,
)


@pytest.mark.setup
def test_setup(juju:Juju):
    """Deploy parca from 1/stable."""
    juju.deploy(
        "parca-k8s",
        PARCA,
        channel="1/stable",
        trust=True,
    )
    juju.wait(
        lambda status: jubilant.all_active(status, PARCA), timeout=1000
    )

def test_upgrade_charm(juju:Juju, parca_charm, parca_resources):
    juju.refresh(
        PARCA,
        path=parca_charm,
        resources=parca_resources
    )
    juju.wait(
        lambda status: jubilant.all_active(status, PARCA), timeout=1000
    )



