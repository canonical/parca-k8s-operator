from contextlib import ExitStack
from unittest.mock import patch

import pytest
from ops.testing import Container, Context

from charm import ParcaOperatorCharm


@pytest.fixture(autouse=True)
def patch_buffer_file_for_charm_tracing(tmp_path):
    with patch(
        "charms.tempo_coordinator_k8s.v0.charm_tracing.BUFFER_DEFAULT_CACHE_FILE_NAME",
        str(tmp_path / "foo.json"),
    ):
        yield


@pytest.fixture
def parca_charm(tmp_path):
    with ExitStack() as stack:
        stack.enter_context(patch("lightkube.core.client.GenericSyncClient"))
        stack.enter_context(patch("nginx.Nginx.are_certificates_on_disk", False))
        stack.enter_context(patch("nginx.CA_CERT_PATH", str(tmp_path / "ca.tmp")))
        yield ParcaOperatorCharm


@pytest.fixture(scope="function")
def context(parca_charm):
    return Context(charm_type=parca_charm)


@pytest.fixture(scope="function")
def nginx_container():
    return Container(
        "nginx",
        can_connect=True,
    )


@pytest.fixture(scope="function")
def parca_container():
    return Container(
        "parca",
        can_connect=True,
    )


@pytest.fixture(scope="function")
def nginx_prometheus_exporter_container():
    return Container(
        "nginx-prometheus-exporter",
        can_connect=True,
    )
