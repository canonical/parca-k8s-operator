# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

import yaml
from pytest import fixture
from pytest_operator.plugin import OpsTest


@fixture(scope="module")
async def parca_charm(ops_test: OpsTest):
    """Parca charm used for integration testing."""
    charm = "./parca-k8s_ubuntu@24.04-amd64.charm"
    if not Path(charm).exists():
        charm = await ops_test.build_charm(".")
    else:
        print("USING CACHED CHARM FILE")
    return charm


@fixture(scope="module")
def parca_resources():
    charmcraft = yaml.safe_load(Path("./charmcraft.yaml").read_text())
    return {
        resource: meta["upstream-source"] for resource, meta in charmcraft["resources"].items()
    }
