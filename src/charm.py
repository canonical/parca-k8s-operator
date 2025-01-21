#!/usr/bin/env python3
# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Parca - a continuous profiling tool."""

import logging
import socket
from typing import Optional, List
from urllib.parse import urlparse

import ops
from charms.catalogue_k8s.v1.catalogue import CatalogueConsumer, CatalogueItem
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.parca_k8s.v0.parca_scrape import ProfilingEndpointConsumer, ProfilingEndpointProvider
from charms.parca_k8s.v0.parca_store import (
    ParcaStoreEndpointProvider,
    ParcaStoreEndpointRequirer,
    RemoveStoreEvent,
)
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer, charm_tracing_config
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer

from nginx import (
    NGINX_PORT,
    NGINX_PROMETHEUS_EXPORTER_PORT,
    Address,
    Nginx,
    NginxPrometheusExporter,
)
from parca import PARCA_PORT, Parca, DEFAULT_CONFIG_PATH as CONFIG_PATH, ScrapeConfig

logger = logging.getLogger(__name__)


@trace_charm(
    tracing_endpoint="charm_tracing_endpoint",
    extra_types=[
        Parca,
        ProfilingEndpointConsumer,
        MetricsEndpointProvider,
        ProfilingEndpointProvider,
        GrafanaDashboardProvider,
        ParcaStoreEndpointProvider,
        ParcaStoreEndpointRequirer,
    ],
)
class ParcaOperatorCharm(ops.CharmBase):
    """Charmed Operator to deploy Parca - a continuous profiling tool."""

    def __init__(self, *args):
        super().__init__(*args)
        self._fqdn = socket.getfqdn()
        self.unit.set_ports(8080)
        self.container = self.unit.get_container("parca")

        # ENDPOINT WRAPPERS
        self.profiling_consumer = ProfilingEndpointConsumer(self)
        self.metrics_endpoint_provider = MetricsEndpointProvider(
            self,
            #  We scrape the nginx exporter and parca itelf (over nginx)
            jobs=_format_scrape_target(NGINX_PROMETHEUS_EXPORTER_PORT)
            + _format_scrape_target(NGINX_PORT),
        )
        self.self_profiling_endpoint_provider = ProfilingEndpointProvider(
            self, jobs=_format_scrape_target(NGINX_PORT), relation_name="self-profiling-endpoint"
        )
        self.grafana_dashboard_provider = GrafanaDashboardProvider(self)
        self.ingress = IngressPerAppRequirer(self, host=self._fqdn, port=NGINX_PORT)
        self.catalogue = CatalogueConsumer(
            self,
            item=CatalogueItem(
                "Parca UI",
                icon="chart-areaspline",
                url=self.external_url,
                description="""Continuous profiling backend. Allows you to collect, store,
                 query and visualize profiles from your distributed deployment.""",
            ),
        )
        self.parca_store_endpoint = ParcaStoreEndpointProvider(
            self, port=NGINX_PORT, insecure=True
        )
        self.store_requirer = ParcaStoreEndpointRequirer(
            self, relation_name="external-parca-store-endpoint"
        )
        self.charm_tracing = TracingEndpointRequirer(
            self, relation_name="charm-tracing", protocols=["otlp_http"]
        )
        # TODO: pass CA path once TLS support is added
        # https://github.com/canonical/parca-k8s-operator/issues/362
        self.charm_tracing_endpoint, _ = charm_tracing_config(self.charm_tracing, None)
        self.grafana_source_provider = GrafanaSourceProvider(
            self, source_type="parca", source_port=str(NGINX_PORT)
        )

        # WORKLOADS
        # these need to be instantiated after `ingress` is, as it accesses self._external_url_path
        self.parca = Parca(container=self.unit.get_container("parca"),
                           scrape_configs = self.profiling_consumer.jobs(),
                           enable_persistence=self.config.get("enable-persistence", None),
                           memory_storage_limit=self.config.get("memory-storage-limit", None),
                           store_config=self.store_requirer.config,
                           path_prefix=self._external_url_path
                           )
        self.nginx_exporter = NginxPrometheusExporter(
            container=self.unit.get_container("nginx-prometheus-exporter"),
        )
        self.nginx = Nginx(
            container=self.unit.get_container("nginx"),
            server_name=self._fqdn,
            address=Address(name="parca", port=PARCA_PORT),
            path_prefix=self._external_url_path,
        )

        # unconditional logic
        self._reconcile()

    @property
    def _scheme(self) -> str:
        # TODO: replace with this when integrating TLS
        # return "https" if self.cert_handler.cert else "http"
        return "http"

    @property
    def internal_url(self) -> str:
        """Return workload's internal URL.

        Used for ingress.
        """
        return f"{self._scheme}://{self._fqdn}:{NGINX_PORT}"

    @property
    def external_url(self) -> str:
        """Return the external hostname if configured, else the internal one."""
        return self.ingress.url or self.internal_url

    def _reconcile(self):
        """Unconditional logic to run regardless of the event we're processing.

        This will ensure all workloads are up and running if the preconditions are met.
        """
        self.nginx.reconcile()
        self.nginx_exporter.reconcile()
        self.parca.reconcile()

    @property
    def _external_url_path(self) -> Optional[str]:
        """The path part of our external url if we are ingressed, else None.

        This is used to configure the parca server so it can resolve its internal links.
        """
        if not self.ingress.is_ready():
            return None
        external_url = urlparse(self.ingress.url)
        # external_url.path already includes a trailing /
        return str(external_url.path) or None

    def _collect_unit_status(self, event:ops.CollectStatusEvent):
        """Set unit status depending on the state."""
        containers_not_ready = [c_name for c_name in {"parca", "nginx", "nginx-prometheus-exporter"} if
                                not self.unit.get_container(c_name).can_connect]

        if containers_not_ready:
            event.add_status(ops.WaitingStatus(f"Waiting for containers: {containers_not_ready}..."))
        else:
            self.unit.set_workload_version(self.parca.version)

        event.add_status(ops.ActiveStatus(f"UI ready at {self.external_url}"))



def _format_scrape_target(port: int)->List[ScrapeConfig]:
    return [{"static_configs": [{"targets": [f"*:{port}"]}]}]


if __name__ == "__main__":  # pragma: nocover
    ops.main(ParcaOperatorCharm)
