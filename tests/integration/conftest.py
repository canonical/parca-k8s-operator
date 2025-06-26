# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
from pathlib import Path

from pytest import fixture
from pytest_jubilant import get_resources, pack

logger= logging.getLogger("conftest")

@fixture(scope="module")
def parca_charm():
    """Parca charm used for integration testing."""
    if charm := os.getenv("CHARM_PATH"):
        logger.info("using parca charm from env")
        return charm
    elif Path(charm:="./parca-k8s_ubuntu@24.04-amd64.charm").exists():
        logger.info("using existing parca charm from ./")
        return charm
    logger.info("packing from ./")
    return pack("./")


@fixture(scope="module")
def parca_resources():
    return get_resources("./")
