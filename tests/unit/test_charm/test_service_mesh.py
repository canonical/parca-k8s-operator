# Copyright 2025 Canonical
# See LICENSE file for licensing details.
"""Unit tests for service mesh authorization policies."""

import pytest
from charms.istio_beacon_k8s.v0.service_mesh import AppPolicy, ServiceMeshConsumer
from ops.testing import Container, Context, State

from charm import ParcaOperatorCharm
from nginx import Nginx


@pytest.fixture
def ctx():
    return Context(charm_type=ParcaOperatorCharm)


@pytest.fixture
def base_state(parca_peers):
    return State(
        leader=True,
        containers={
            Container("parca", can_connect=True),
            Container("nginx", can_connect=True),
            Container("nginx-prometheus-exporter", can_connect=True),
        },
        relations={parca_peers},
    )


class TestMeshPolicies:
    """Tests for the _mesh_policies property."""

    def test_mesh_policies_covers_http_and_grpc_ports(self, ctx, base_state):
        # GIVEN a default state
        # WHEN we run any event that instantiates the charm
        with ctx(ctx.on.config_changed(), base_state) as manager:
            charm = manager.charm
            policies = charm._mesh_policies

        # THEN we get AppPolicy objects for each expected relation
        assert all(isinstance(p, AppPolicy) for p in policies)

    def test_grafana_source_policy_allows_http_port(self, ctx, base_state):
        # GIVEN a default state
        # WHEN the charm is instantiated
        with ctx(ctx.on.config_changed(), base_state) as manager:
            charm = manager.charm
            policies = charm._mesh_policies

        # THEN there's a policy for grafana-source on the HTTP port
        grafana_policy = next(p for p in policies if p.relation == "grafana-source")
        ports = [port for e in grafana_policy.endpoints for port in e.ports]
        assert Nginx.parca_http_server_port in ports

    def test_parca_store_policy_allows_grpc_port(self, ctx, base_state):
        # GIVEN a default state
        # WHEN the charm is instantiated
        with ctx(ctx.on.config_changed(), base_state) as manager:
            charm = manager.charm
            policies = charm._mesh_policies

        # THEN there's a policy for parca-store-endpoint on the gRPC port
        store_policy = next(p for p in policies if p.relation == "parca-store-endpoint")
        ports = [port for e in store_policy.endpoints for port in e.ports]
        assert Nginx.parca_grpc_server_port in ports

    def test_self_profiling_policy_allows_http_port(self, ctx, base_state):
        # GIVEN a default state
        # WHEN the charm is instantiated
        with ctx(ctx.on.config_changed(), base_state) as manager:
            charm = manager.charm
            policies = charm._mesh_policies

        # THEN there's a policy for self-profiling-endpoint on the HTTP port
        profiling_policy = next(p for p in policies if p.relation == "self-profiling-endpoint")
        ports = [port for e in profiling_policy.endpoints for port in e.ports]
        assert Nginx.parca_http_server_port in ports

    def test_grpc_port_not_in_http_only_policies(self, ctx, base_state):
        # GIVEN a default state
        # WHEN the charm is instantiated
        with ctx(ctx.on.config_changed(), base_state) as manager:
            charm = manager.charm
            policies = charm._mesh_policies

        # THEN grafana-source does NOT get access to the gRPC port (it only needs HTTP)
        grafana_policy = next(p for p in policies if p.relation == "grafana-source")
        ports = [port for e in grafana_policy.endpoints for port in e.ports]
        assert Nginx.parca_grpc_server_port not in ports

    def test_service_mesh_consumer_registered(self, ctx, base_state):
        # GIVEN a default state
        # WHEN the charm is instantiated
        with ctx(ctx.on.config_changed(), base_state) as manager:
            charm = manager.charm

        # THEN the service_mesh attribute is a ServiceMeshConsumer
        assert isinstance(charm.service_mesh, ServiceMeshConsumer)
