import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from ops.testing import Container, Context, PeerRelation, Relation

from charm import ParcaOperatorCharm


@pytest.fixture(autouse=True)
def patch_buffer_file_for_charm_tracing(tmp_path):
    with patch(
            "charms.tempo_coordinator_k8s.v0.charm_tracing.BUFFER_DEFAULT_CACHE_FILE_NAME",
            str(tmp_path / "foo.json"),
    ):
        yield


@pytest.fixture(autouse=True)
def patch_all(tmp_path):
    ca_tmp_path = tmp_path / "ca.tmp"
    with ExitStack() as stack:
        stack.enter_context(patch("lightkube.core.client.GenericSyncClient"))
        stack.enter_context(patch("nginx.Nginx._are_certificates_on_disk", False))
        stack.enter_context(patch("nginx.CA_CERT_PATH", str(ca_tmp_path)))
        stack.enter_context(patch("charm.CA_CERT_PATH", str(ca_tmp_path)))
        stack.enter_context(patch("parca.CA_CERT_PATH", str(ca_tmp_path)))
        stack.enter_context(patch("parca.Parca.version", "v0.12.0"))
        stack.enter_context(
            patch(
                "charm.JujuTopology.from_charm",
                MagicMock(return_value=MagicMock(as_dict=MagicMock(return_value={}))),
            )
        )
        yield


@pytest.fixture(scope="function")
def context():
    return Context(charm_type=ParcaOperatorCharm)


@pytest.fixture
def parca_peers():
    return PeerRelation("parca-peers")


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


@pytest.fixture
def workload_tracing_relation():
    remote_app_databag = {
        "receivers": json.dumps(
            [{"protocol": {"name": "otlp_grpc", "type": "grpc"}, "url": "192.0.2.0/24"}]
        )
    }
    return Relation(
        "workload-tracing", remote_app_name="backend", remote_app_data=remote_app_databag
    )
