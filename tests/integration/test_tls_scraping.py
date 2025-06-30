#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.


import jubilant
import pytest
from helpers import PARCA, get_parca_ingested_label_values, query_parca_server
from jubilant import Juju
from tenacity import retry
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential as wexp

PARCA_TESTER = "parca-tester"
SSC = "ssc"
# Path where SSC saves the CA certificate
SSC_CA_CERT_PATH = "/tmp/ca-cert.pem"


@pytest.mark.setup
def test_setup(juju:Juju, parca_charm, parca_resources):
    """Test that Parca can be related with Self Signed Certificates for TLS."""
    juju.deploy(
        parca_charm,
        PARCA,
        resources=parca_resources,
        trust=True,
    )
    juju.deploy(
        "self-signed-certificates",
        SSC,
        channel="latest/edge",
        trust=True,
    )
    juju.integrate(f"{PARCA}:certificates", SSC)

    # Wait for the two apps to quiesce
    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, SSC), timeout=1000
    )


def test_direct_url_200(juju:Juju):
    exit_code, output = query_parca_server(
        juju.model, SSC, tls=True, ca_cert_path=SSC_CA_CERT_PATH
    )
    assert exit_code == 0, f"Failed to query the parca server. {output}"


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
def test_parca_is_scraping_itself(juju:Juju):
    label_values = get_parca_ingested_label_values(juju.model, label="juju_application", tls=True)
    assert "parca" in label_values


@pytest.mark.setup
def test_deploy_parca_tester(juju:Juju, parca_charm, parca_resources):
    # Deploy and integrate tester charm
    juju.deploy(
        parca_charm,
        PARCA_TESTER,
        resources=parca_resources,
        trust=True,
    )

    juju.integrate(PARCA, f"{PARCA_TESTER}:self-profiling-endpoint"),
    juju.integrate(f"{PARCA_TESTER}:certificates", SSC),

    juju.wait(
        lambda status: jubilant.all_active(status, PARCA, PARCA_TESTER), timeout=1000
    )

@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
def test_parca_is_scraping_parca_tester(juju:Juju):
    label_values = get_parca_ingested_label_values(juju.model, label="juju_application", tls=True)
    assert "parca-tester" in label_values


@pytest.mark.teardown
def test_remove_tls(juju:Juju):
    juju.remove_relation(PARCA + ":certificates", SSC + ":certificates")
    # we need to wait for a while until parca's nginx loses the TLS connection
    juju.wait(
        lambda status: jubilant.all_active(status, PARCA), timeout=1000
    )


@retry(wait=wexp(multiplier=2, min=1, max=30), stop=stop_after_attempt(10), reraise=True)
def test_direct_url_400(juju:Juju):
    exit_code, _ = query_parca_server(
        juju.model, SSC, tls=True, ca_cert_path=SSC_CA_CERT_PATH
    )
    assert exit_code != 0


@pytest.mark.teardown
def test_remove_parca(juju:Juju):
    juju.remove_application(PARCA)
