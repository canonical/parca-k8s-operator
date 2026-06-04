import jubilant
import pytest
from jubilant import Juju

from tests.integration.helpers import (
    PARCA,
)

# Cross-base upgrades (e.g. 24.04 -> 26.04) are not supported via juju refresh.
# The charmhub charm is built for 24.04 (Python 3.12), while the local charm
# targets 26.04 (Python 3.14). Juju refresh only replaces charm code, not the
# container image, so the old container's Python cannot load the new venv.
pytestmark = pytest.mark.skip(reason="Cross-base upgrade from 24.04 to 26.04 not supported")


@pytest.mark.juju_setup
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



