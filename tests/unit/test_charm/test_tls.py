# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import socket
from datetime import timedelta

import pytest
from charms.tls_certificates_interface.v4.tls_certificates import (
    LIBID,
    generate_ca,
    generate_certificate,
    generate_csr,
    generate_private_key,
)
from scenario import Relation, Secret, State

from charm import ParcaOperatorCharm


@pytest.fixture
def private_key():
    return generate_private_key()


@pytest.fixture
def csr(private_key):
    return generate_csr(
        private_key=private_key, common_name="parca-k8s", sans_dns=[socket.getfqdn()]
    )


@pytest.fixture
def ca(private_key):
    return generate_ca(
        private_key=private_key,
        common_name="parca-k8s",
        validity=timedelta(hours=1),
    )


@pytest.fixture
def certificate(private_key, ca, csr):
    return generate_certificate(
        ca_private_key=private_key,
        csr=csr,
        ca=ca,
        validity=timedelta(hours=1),
    )


@pytest.fixture
def certificates(csr, certificate, ca):
    return Relation(
        "certificates",
        remote_app_data={
            "certificates": json.dumps(
                [
                    {
                        "ca": ca.raw,
                        "certificate_signing_request": csr.raw,
                        "certificate": certificate.raw,
                    }
                ]
            )
        },
        local_unit_data={
            "certificate_signing_requests": json.dumps(
                [{"certificate_signing_request": csr.raw, "ca": False}]
            )
        },
    )


@pytest.fixture
def base_state(
    parca_container,
    nginx_container,
    nginx_prometheus_exporter_container,
    certificates,
    private_key,
):
    private_key_secret = Secret(
        {"private-key": private_key.raw},
        label=f"{LIBID}-private-key-0",
        owner="unit",
    )
    return State(
        leader=True,
        relations=[certificates],
        containers=[parca_container, nginx_container, nginx_prometheus_exporter_container],
        secrets={private_key_secret},
    )


def test_endpoint_with_tls_enabled(
    context,
    base_state,
    certificates,
):
    # GIVEN a charm with certificates relation
    # WHEN we process any event
    with context(context.on.relation_changed(certificates), base_state) as mgr:
        charm: ParcaOperatorCharm = mgr.charm
        # THEN we have TLS enabled
        assert charm._tls_ready
        assert charm._external_url.startswith("https://")


def test_endpoint_with_tls_disabled(
    context,
    base_state,
    certificates,
):
    # GIVEN a charm with a broken certificates relation
    # WHEN we process any event
    with context(context.on.relation_broken(certificates), base_state) as mgr:
        charm: ParcaOperatorCharm = mgr.charm
        # THEN we have TLS disabled
        assert not charm._tls_ready
        assert charm._external_url.startswith("http://")

