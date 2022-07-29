# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

import yaml
from pytest import fixture
from pytest_operator.plugin import OpsTest


@fixture(scope="module")
async def parca_charm(ops_test: OpsTest):
    """Parca charm used for integration testing."""
    charm = await ops_test.build_charm(".")
    return charm


@fixture(scope="module")
async def parca_oci_image(ops_test: OpsTest):
    meta = yaml.safe_load(Path("./metadata.yaml").read_text())
    return meta["resources"]["parca-image"]["upstream-source"]
