# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import tempfile
from pathlib import Path
from subprocess import getstatusoutput

import yaml
from pytest import fixture
from pytest_operator.plugin import OpsTest

from nginx import CA_CERT_PATH


@fixture(scope="module")
async def parca_charm(ops_test: OpsTest):
    """Parca charm used for integration testing."""
    charm = await ops_test.build_charm(".")
    return charm


@fixture(scope="module")
def parca_resources():
    charmcraft = yaml.safe_load(Path("./charmcraft.yaml").read_text())
    return {
        resource: meta["upstream-source"] for resource, meta in charmcraft["resources"].items()
    }


@fixture(scope="function")
def ca_cert(ops_test: OpsTest):
    with tempfile.NamedTemporaryFile() as f:
        p = Path(f.name)
        exit_code, output = getstatusoutput(
            f"""juju scp --model {ops_test.model_name} parca/0:{CA_CERT_PATH} {p.absolute()}."""
        )
        if exit_code != 0:
            assert False, f"Unable to copy certificate CA from parca/0. {output}"
        yield p
