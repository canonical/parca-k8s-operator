import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import List
from unittest.mock import patch

import ops
import pytest
from ops import testing

from nginx import (
    CA_CERT_PATH,
    CERT_PATH,
    KEY_PATH,
    Address,
    Nginx,
    NginxConfig,
    _get_dns_ip_address,
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
        assert nginx.are_certificates_on_disk


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
        nginx.delete_certificates()

        # THEN the certs get deleted from disk
        assert not nginx.are_certificates_on_disk


@pytest.mark.parametrize(
    "address",
    (Address("foo", 123), Address("bar", 42)),
)
def test_nginx_config_is_list_before_crossplane(address):
    nginx = NginxConfig("localhost", False)
    prepared_config = nginx._prepare_config(address)
    assert isinstance(prepared_config, List)


@pytest.mark.parametrize(
    "address",
    (Address("foo", 123), Address("bar", 42)),
)
def test_nginx_config_is_parsed_by_crossplane(address):
    nginx = NginxConfig("localhost", False)
    prepared_config = nginx.config(address)
    assert isinstance(prepared_config, str)


@pytest.mark.parametrize(
    "address",
    (Address("foo", 123), Address("bar", 42)),
)
@pytest.mark.parametrize("tls", (True, False))
@pytest.mark.parametrize("hostname", ("localhost", "foobarhost"))
def test_nginx_config_contains_upstreams_and_proxy_pass(
    context, nginx_container, address, tls, hostname
):
    with mock_resolv_conf(f"nameserver {sample_dns_ip}"):
        nginx = NginxConfig(hostname, False)

    prepared_config = nginx.config(address)
    assert f"resolver {sample_dns_ip};" in prepared_config

    assert "listen 8080" in prepared_config
    assert "listen [::]:8080" in prepared_config

    sanitised_name = address.name.replace("_", "-")
    assert f"upstream {sanitised_name}" in prepared_config
    assert f"set $backend http{'s' if tls else ''}://{sanitised_name}"
    assert "proxy_pass $backend" in prepared_config


@contextmanager
def mock_resolv_conf(contents: str):
    with tempfile.NamedTemporaryFile() as tf:
        Path(tf.name).write_text(contents)
        with patch("nginx.RESOLV_CONF_PATH", tf.name):
            yield


@pytest.mark.parametrize(
    "mock_contents, expected_dns_ip",
    (
        (f"foo bar\nnameserver {sample_dns_ip}", sample_dns_ip),
        (f"nameserver {sample_dns_ip}\n foo bar baz", sample_dns_ip),
        (f"foo bar\nfoo bar\nnameserver {sample_dns_ip}\nnameserver 198.18.0.1", sample_dns_ip),
    ),
)
def test_dns_ip_addr_getter(mock_contents, expected_dns_ip):
    with mock_resolv_conf(mock_contents):
        assert _get_dns_ip_address() == expected_dns_ip


def test_dns_ip_addr_fail():
    with pytest.raises(RuntimeError):
        with mock_resolv_conf("foo bar"):
            _get_dns_ip_address()
