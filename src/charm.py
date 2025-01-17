#!/usr/bin/env python3
# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Parca - a continuous profiling tool."""

import logging
import socket
from typing import Optional
from urllib.parse import urlparse

import ops
from charms.catalogue_k8s.v1.catalogue import CatalogueConsumer, CatalogueItem
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.parca_k8s.v0.parca_config import DEFAULT_CONFIG_PATH as CONFIG_PATH
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
from parca import PARCA_PORT, Parca

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
        self.parca = Parca()
        # The profiling_consumer handles the relation that allows Parca to scrape other apps in the
        # model that provide a "profiling-endpoint" relation.
        self.profiling_consumer = ProfilingEndpointConsumer(self)
        self.framework.observe(
            self.profiling_consumer.on.targets_changed, self._configure_and_start
        )

        # The metrics_endpoint_provider enables Parca to be scraped by Prometheus for metrics.
        self.metrics_endpoint_provider = MetricsEndpointProvider(
            self, jobs=_format_scrape_target(NGINX_PROMETHEUS_EXPORTER_PORT)
        )

        # The self_profiling_endpoint_provider enables Parca to profile itself.
        self.self_profiling_endpoint_provider = ProfilingEndpointProvider(
            self, jobs=_format_scrape_target(NGINX_PORT), relation_name="self-profiling-endpoint"
        )

        # Allow Parca to provide dashboards to Grafana over a relation.
        self.grafana_dashboard_provider = GrafanaDashboardProvider(self)

        self.ingress = IngressPerAppRequirer(self, host=self._fqdn, port=NGINX_PORT)
        # this needs to be instantiated after `ingress` is
        self.nginx = Nginx(
            container=self.unit.get_container("nginx"),
            server_name=self._fqdn,
            address=Address(name="parca", port=PARCA_PORT),
            path_prefix=self._external_url_path,
        )
        self.nginx_exporter = NginxPrometheusExporter(
            container=self.unit.get_container("nginx-prometheus-exporter"),
        )

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

        # Enable Parca agents or Parca servers to use this instance as a store.
        self.parca_store_endpoint = ParcaStoreEndpointProvider(
            self, port=PARCA_PORT, insecure=True
        )

        # Enable the option to send profiles to a remote store (i.e. Polar Signals Cloud).
        self.store_requirer = ParcaStoreEndpointRequirer(
            self, relation_name="external-parca-store-endpoint"
        )
        # Enable charm tracing
        self.charm_tracing = TracingEndpointRequirer(
            self, relation_name="charm-tracing", protocols=["otlp_http"]
        )
        # TODO: pass CA path once TLS support is added
        # https://github.com/canonical/parca-k8s-operator/issues/362
        self.charm_tracing_endpoint, _ = charm_tracing_config(self.charm_tracing, None)

        self.grafana_source_provider = GrafanaSourceProvider(
            self, source_type="parca", source_port=str(PARCA_PORT)
        )

        # conditional logic
        # we must configure and start when pebble-ready or config-changed show up
        self.framework.observe(self.on.parca_pebble_ready, self._configure_and_start)
        self.framework.observe(self.on.config_changed, self._configure_and_start)
        # we may reconfigure if the store config has changed
        self.framework.observe(self.store_requirer.on.endpoints_changed, self._configure_and_start)
        self.framework.observe(self.store_requirer.on.remove_store, self._configure_and_start)
        # we may reconfigure on ingress changes, so that the path-prefix is updated
        self.framework.observe(self.ingress.on.ready, self._configure_and_start)
        self.framework.observe(self.ingress.on.revoked, self._configure_and_start)

        # generic status check
        self.framework.observe(self.on.update_status, self._update_status)

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
        return f"{self._scheme}://{self._fqdn}:{PARCA_PORT}"

    @property
    def external_url(self) -> str:
        """Return the external hostname if configured, else the internal one."""
        return self.ingress.url or self.internal_url

    def _reconcile(self):
        """Unconditional logic to run regardless of the event we're processing."""
        self.nginx.configure_pebble_layer()
        self.nginx_exporter.configure_pebble_layer()

    def _update_status(self, _):
        """Handle the update status hook on an interval dictated by model config."""
        self.unit.set_workload_version(self.parca.version)

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

    def _configure_and_start(self, event):
        """Start Parca having (re)configured it with the relevant jobs."""
        # TODO:
        #  - call this method from _reconcile
        #  - remove all observers
        #  - check for config changes by pulling from the container and comparing
        #  - only set maintenance if there are changes
        self.unit.status = ops.MaintenanceStatus("reconfiguring parca")

        if self.container.can_connect():
            # Grab the scrape configs and push a generated config file into the container
            # Parca will automatically reload its config on changes
            config = self.parca.generate_config(self.profiling_consumer.jobs())
            self.container.push(CONFIG_PATH, str(config), make_dirs=True, permissions=0o644)

            # Remove all store configs on a RemoveStoreEvent, else grab store config from relation
            store_conf = {} if isinstance(event, RemoveStoreEvent) else self.store_requirer.config

            # Add an updated Pebble layer to the container
            # Add a config hash to the layer so if the config changes, replan restarts the service
            layer = self.parca.pebble_layer(
                self.config, store_conf, path_prefix=self._external_url_path
            )
            self.container.add_layer("parca", layer, combine=True)
            self.container.replan()

            self.unit.set_workload_version(self.parca.version)
            self.unit.status = ops.ActiveStatus(
                f"UI ready at {self.ingress.url}" if self.ingress.url else ""
            )
        else:
            self.unit.status = ops.WaitingStatus("waiting for container")


def _format_scrape_target(port: int):
    return [{"static_configs": [{"targets": [f"*:{port}"]}]}]


if __name__ == "__main__":  # pragma: nocover
    ops.main(ParcaOperatorCharm)
