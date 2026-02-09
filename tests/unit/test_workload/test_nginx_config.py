import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import ops
import pytest
from charmlibs.nginx_k8s import NginxConfig
from ops import testing

from nginx import (
    CA_CERT_PATH,
    CERT_PATH,
    KEY_PATH,
    Address,
    Nginx,
)

logger = logging.getLogger(__name__)
sample_dns_ip = "198.18.0.0"


@pytest.fixture
def certificate_mounts():
    temp_files = {}
    for path in {KEY_PATH, CERT_PATH, CA_CERT_PATH}:
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        temp_files[path] = temp_file

    mounts = {}
    for cert_path, temp_file in temp_files.items():
        mounts[cert_path] = testing.Mount(location=cert_path, source=temp_file.name)

    return mounts


@pytest.fixture
def nginx_context():
    return testing.Context(
        ops.CharmBase, meta={"name": "foo", "containers": {"nginx": {"type": "oci-image"}}}
    )


def test_certs_on_disk(certificate_mounts: dict, nginx_context: testing.Context):
    # GIVEN any charm with a container
    ctx = nginx_context

    # WHEN we process any event
    with ctx(
        ctx.on.update_status(),
        state=testing.State(
            containers={testing.Container("nginx", can_connect=True, mounts=certificate_mounts)}
        ),
    ) as mgr:
        charm = mgr.charm
        nginx = Nginx(charm.unit.get_container("nginx"), "test", None)

        # THEN the certs exist on disk
        assert nginx._are_certificates_on_disk


def test_certs_deleted(certificate_mounts: dict, nginx_context: testing.Context):
    # Test deleting the certificates.

    # GIVEN any charm with a container
    ctx = nginx_context

    # WHEN we process any event
    with ctx(
        ctx.on.update_status(),
        state=testing.State(
            containers={
                testing.Container(
                    "nginx",
                    can_connect=True,
                    mounts=certificate_mounts,
                )
            }
        ),
    ) as mgr:
        charm = mgr.charm
        nginx = Nginx(charm.unit.get_container("nginx"), "test", None)

        # AND when we call delete_certificates
        nginx._delete_certificates()

        # THEN the certs get deleted from disk
        assert not nginx._are_certificates_on_disk


@pytest.mark.parametrize("ipv6", (True, False))
@pytest.mark.parametrize(
    "address",
    (Address("foo", 123), Address("bar", 42)),
)
@pytest.mark.parametrize("tls", (True, False))
@pytest.mark.parametrize("hostname", ("localhost", "foobarhost"))
@pytest.mark.parametrize("http_port", (42, 43))
@pytest.mark.parametrize("grpc_port", (44, 45))
def test_nginx_config_contains_upstreams_and_proxy_pass(
    address, tls, hostname, http_port, grpc_port, ipv6
):
    with mock_ipv6(ipv6):
        with mock_resolv_conf(f"nameserver {sample_dns_ip}"):
            nginx = Nginx(testing.Container(
                    "nginx",
                    can_connect=True,
                    mounts=certificate_mounts,
                ), hostname, address, None)
            # override class attributes
            nginx.parca_grpc_server_port = grpc_port
            nginx.parca_http_server_port = http_port

            nginx_config = NginxConfig(hostname, nginx._nginx_upstreams(), nginx._server_ports_to_locations())

    prepared_config = nginx_config.get_config(nginx._upstreams_to_addresses(), tls)

    assert f"resolver {sample_dns_ip};" in prepared_config
    assert f"listen {http_port}" in prepared_config
    assert (
        (f"listen [::]:{http_port}" in prepared_config)
        if ipv6
        else (f"listen [::]:{http_port}" not in prepared_config)
    )

    assert f"listen {grpc_port}" in prepared_config
    assert (
        (f"listen [::]:{grpc_port}" in prepared_config)
        if ipv6
        else (f"listen [::]:{grpc_port}" not in prepared_config)
    )
    assert f"upstream {address.name}" in prepared_config
    assert f"set $backend http://{address.name}" in prepared_config
    assert "proxy_pass $backend" in prepared_config


@contextmanager
def mock_ipv6(enable: bool):
    with patch("charmlibs.nginx_k8s._config._is_ipv6_enabled", MagicMock(return_value=enable)):
        yield


@contextmanager
def mock_resolv_conf(contents: str):
    with tempfile.NamedTemporaryFile() as tf:
        Path(tf.name).write_text(contents)
        with patch("charmlibs.nginx_k8s._config.RESOLV_CONF_PATH", tf.name):
            yield

