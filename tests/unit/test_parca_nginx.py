import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from nginx import Address, NginxConfig, _get_dns_ip_address

logger = logging.getLogger(__name__)
sample_dns_ip = "198.18.0.0"


@pytest.mark.parametrize(
    "address",
    (Address("foo", 123), Address("bar", 42)),
)
def test_nginx_config_is_list_before_crossplane(context, nginx_container, address):
    nginx = NginxConfig("localhost", False)
    prepared_config = nginx._prepare_config(address)
    assert isinstance(prepared_config, List)


@pytest.mark.parametrize(
    "address",
    (Address("foo", 123), Address("bar", 42)),
)
def test_nginx_config_is_parsed_by_crossplane(context, nginx_container, address):
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
