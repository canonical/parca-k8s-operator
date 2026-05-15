# Copyright 2025 Canonical
# See LICENSE file for licensing details.
"""Unit tests for the Istio ingress integration."""


import pytest
from ops.testing import Relation, State

from charm import ParcaOperatorCharm
from nginx import Nginx


# Remote-app databags that istio-ingress publishes back to the requirer
def _istio_app_data(external_host: str = "istio.example.com", tls_enabled: bool = False):
    return {"external_host": external_host, "tls_enabled": str(tls_enabled)}


@pytest.fixture
def istio_ingress_relation(request):
    """Parametrised fixture: pass remote_app_data to override defaults."""
    app_data = getattr(request, "param", _istio_app_data())
    return Relation("istio-ingress", remote_app_data=app_data)


@pytest.fixture
def base_state(parca_peers, parca_container, nginx_container, nginx_prometheus_exporter_container):
    return State(
        leader=True,
        containers={parca_container, nginx_container, nginx_prometheus_exporter_container},
        relations={parca_peers},
    )


# ---------------------------------------------------------------------------
# URL derivation
# ---------------------------------------------------------------------------


class TestHttpServerUrl:
    def test_no_ingress_uses_internal_fqdn(self, context, base_state):
        # GIVEN no ingress relation
        # WHEN config-changed fires
        with context(context.on.config_changed(), base_state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            # THEN http_server_url is fqdn-based with http scheme
            assert f":{Nginx.parca_http_server_port}" in charm.http_server_url
            assert charm.http_server_url.startswith("http://")

    @pytest.mark.parametrize(
        "istio_ingress_relation",
        [_istio_app_data("parca.k8s.local", tls_enabled=False)],
        indirect=True,
    )
    def test_istio_ingress_http_url(self, context, base_state, istio_ingress_relation):
        # GIVEN an istio-ingress relation with no TLS
        state = State(
            leader=True,
            containers=base_state.containers,
            relations=base_state.relations | {istio_ingress_relation},
        )
        # WHEN relation-changed fires
        with context(context.on.relation_changed(istio_ingress_relation), state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            # THEN http_server_url uses the istio external host with http
            assert charm.http_server_url == f"http://parca.k8s.local:{Nginx.parca_http_server_port}"

    @pytest.mark.parametrize(
        "istio_ingress_relation",
        [_istio_app_data("parca.k8s.local", tls_enabled=True)],
        indirect=True,
    )
    def test_istio_ingress_https_url(self, context, base_state, istio_ingress_relation):
        # GIVEN an istio-ingress relation with TLS enabled
        state = State(
            leader=True,
            containers=base_state.containers,
            relations=base_state.relations | {istio_ingress_relation},
        )
        # WHEN relation-changed fires
        with context(context.on.relation_changed(istio_ingress_relation), state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            # THEN http_server_url uses https
            assert charm.http_server_url == f"https://parca.k8s.local:{Nginx.parca_http_server_port}"


class TestGrpcServerUrl:
    @pytest.mark.parametrize(
        "istio_ingress_relation",
        [_istio_app_data("parca.k8s.local", tls_enabled=False)],
        indirect=True,
    )
    def test_istio_ingress_grpc_url(self, context, base_state, istio_ingress_relation):
        # GIVEN an istio-ingress relation
        state = State(
            leader=True,
            containers=base_state.containers,
            relations=base_state.relations | {istio_ingress_relation},
        )
        # WHEN relation-changed fires
        with context(context.on.relation_changed(istio_ingress_relation), state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            # THEN grpc_server_url uses the istio external host (no scheme)
            assert charm.grpc_server_url == f"parca.k8s.local:{Nginx.parca_grpc_server_port}"

    def test_no_ingress_grpc_url_uses_fqdn(self, context, base_state):
        # GIVEN no ingress relation
        with context(context.on.config_changed(), base_state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            assert f":{Nginx.parca_grpc_server_port}" in charm.grpc_server_url


class TestScheme:
    @pytest.mark.parametrize(
        "istio_ingress_relation",
        [_istio_app_data(tls_enabled=True)],
        indirect=True,
    )
    def test_scheme_is_https_when_istio_tls_enabled(
        self, context, base_state, istio_ingress_relation
    ):
        state = State(
            leader=True,
            containers=base_state.containers,
            relations=base_state.relations | {istio_ingress_relation},
        )
        with context(context.on.relation_changed(istio_ingress_relation), state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            assert charm._scheme == "https"

    @pytest.mark.parametrize(
        "istio_ingress_relation",
        [_istio_app_data(tls_enabled=False)],
        indirect=True,
    )
    def test_scheme_is_http_when_istio_tls_disabled(
        self, context, base_state, istio_ingress_relation
    ):
        state = State(
            leader=True,
            containers=base_state.containers,
            relations=base_state.relations | {istio_ingress_relation},
        )
        with context(context.on.relation_changed(istio_ingress_relation), state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            assert charm._scheme == "http"

    def test_scheme_defaults_to_http_without_ingress(self, context, base_state):
        with context(context.on.config_changed(), base_state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            assert charm._scheme == "http"


# ---------------------------------------------------------------------------
# is_ready flags
# ---------------------------------------------------------------------------


class TestIsReady:
    def test_istio_not_ready_without_relation(self, context, base_state):
        with context(context.on.config_changed(), base_state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            assert not charm._is_istio_ingress_ready

    @pytest.mark.parametrize(
        "istio_ingress_relation",
        [_istio_app_data("parca.k8s.local")],
        indirect=True,
    )
    def test_istio_ready_with_relation_and_host(
        self, context, base_state, istio_ingress_relation
    ):
        state = State(
            leader=True,
            containers=base_state.containers,
            relations=base_state.relations | {istio_ingress_relation},
        )
        with context(context.on.relation_changed(istio_ingress_relation), state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            assert charm._is_istio_ingress_ready

    def test_istio_not_ready_without_external_host(self, context, base_state):
        # GIVEN a relation with no external_host published yet
        relation = Relation("istio-ingress", remote_app_data={"external_host": "", "tls_enabled": "False"})
        state = State(
            leader=True,
            containers=base_state.containers,
            relations=base_state.relations | {relation},
        )
        with context(context.on.relation_changed(relation), state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            assert not charm._is_istio_ingress_ready


# ---------------------------------------------------------------------------
# Config submitted to istio-ingress
# ---------------------------------------------------------------------------


class TestIstioIngressConfig:
    def test_istio_ingress_config_has_two_listeners(self, context, base_state):
        # GIVEN a leader unit with istio-ingress relation
        istio_rel = Relation("istio-ingress", remote_app_data=_istio_app_data("parca.k8s.local"))
        state = State(
            leader=True,
            containers=base_state.containers,
            relations=base_state.relations | {istio_rel},
        )
        with context(context.on.relation_changed(istio_rel), state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            cfg = charm._istio_ingress_config
            # THEN there are two listeners: one HTTP and one gRPC
            assert len(cfg.listeners) == 2
            ports = {lst.port for lst in cfg.listeners}
            assert Nginx.parca_http_server_port in ports
            assert Nginx.parca_grpc_server_port in ports

    def test_istio_ingress_config_routes_to_app(self, context, base_state):
        # GIVEN a leader unit with istio-ingress relation
        istio_rel = Relation("istio-ingress", remote_app_data=_istio_app_data("parca.k8s.local"))
        state = State(
            leader=True,
            containers=base_state.containers,
            relations=base_state.relations | {istio_rel},
        )
        with context(context.on.relation_changed(istio_rel), state) as mgr:
            charm: ParcaOperatorCharm = mgr.charm
            cfg = charm._istio_ingress_config
            # THEN each route has a BackendRef pointing to this application
            for route in cfg.http_routes + cfg.grpc_routes:
                assert all(ref.service == charm.app.name for ref in route.backends)

    def test_config_submitted_on_reconcile(self, context, base_state):
        # GIVEN a leader unit with istio-ingress relation
        istio_rel = Relation("istio-ingress", remote_app_data=_istio_app_data("parca.k8s.local"))
        state = State(
            leader=True,
            containers=base_state.containers,
            relations=base_state.relations | {istio_rel},
        )
        # WHEN reconcile fires
        state_out = context.run(context.on.relation_changed(istio_rel), state)
        # THEN the charm writes config to the relation databag
        rel_out = state_out.get_relation(istio_rel.id)
        assert "config" in rel_out.local_app_data

    def test_config_not_submitted_by_non_leader(self, context, base_state):
        # GIVEN a non-leader unit with istio-ingress relation
        istio_rel = Relation("istio-ingress", remote_app_data=_istio_app_data("parca.k8s.local"))
        state = State(
            leader=False,
            containers=base_state.containers,
            relations=base_state.relations | {istio_rel},
        )
        # WHEN reconcile fires
        state_out = context.run(context.on.relation_changed(istio_rel), state)
        # THEN no config is written (non-leader cannot write app databag)
        rel_out = state_out.get_relation(istio_rel.id)
        assert "config" not in rel_out.local_app_data
