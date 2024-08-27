#!/usr/bin/env python3
# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Parca - a continuous profiling tool."""

import logging

import ops
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.parca.v0.parca_config import DEFAULT_CONFIG_PATH as CONFIG_PATH
from charms.parca.v0.parca_scrape import ProfilingEndpointConsumer, ProfilingEndpointProvider
from charms.parca.v0.parca_store import (
    ParcaStoreEndpointProvider,
    ParcaStoreEndpointRequirer,
    RemoveStoreEvent,
)
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer

from parca import Parca

logger = logging.getLogger(__name__)


class ParcaOperatorCharm(ops.CharmBase):
    """Charmed Operator to deploy Parca - a continuous profiling tool."""

    def __init__(self, *args):
        super().__init__(*args)

        self.container = self.unit.get_container("parca")
        self.parca = Parca()

        self.framework.observe(self.on.parca_pebble_ready, self._configure_and_start)
        self.framework.observe(self.on.config_changed, self._configure_and_start)
        self.framework.observe(self.on.update_status, self._update_status)

        # The profiling_consumer handles the relation that allows Parca to scrape other apps in the
        # model that provide a "profiling-endpoint" relation.
        self.profiling_consumer = ProfilingEndpointConsumer(self)
        self.framework.observe(
            self.profiling_consumer.on.targets_changed, self._configure_and_start
        )

        self._scrape_targets = [{"static_configs": [{"targets": [f"*:{self.parca.port}"]}]}]

        # The metrics_endpoint_provider enables Parca to be scraped by Prometheus for metrics.
        self.metrics_endpoint_provider = MetricsEndpointProvider(self, jobs=self._scrape_targets)

        # The self_profiling_endpoint_provider enables Parca to profile itself.
        self.self_profiling_endpoint_provider = ProfilingEndpointProvider(
            self, jobs=self._scrape_targets, relation_name="self-profiling-endpoint"
        )

        # Allow Parca to provide dashboards to Grafana over a relation.
        self.grafana_dashboard_provider = GrafanaDashboardProvider(self)

        self.ingress = IngressPerAppRequirer(
            self, host=f"{self.app.name}.{self.model.name}.svc.cluster.local", port=self.parca.port
        )

        # Enable Parca agents or Parca servers to use this instance as a store.
        self.parca_store_endpoint = ParcaStoreEndpointProvider(
            self, port=self.parca.port, insecure=True
        )

        # Enable the option to send profiles to a remote store (i.e. Polar Signals Cloud).
        self.store_requirer = ParcaStoreEndpointRequirer(
            self, relation_name="external-parca-store-endpoint"
        )
        self.framework.observe(self.store_requirer.on.endpoints_changed, self._configure_and_start)
        self.framework.observe(self.store_requirer.on.remove_store, self._configure_and_start)

    def _update_status(self, _):
        """Handle the update status hook on an interval dictated by model config."""
        self.unit.set_workload_version(self.parca.version)

    def _configure_and_start(self, event):
        """Start Parca having (re)configured it with the relevant jobs."""
        self.unit.status = ops.MaintenanceStatus("reconfiguring parca")

        if self.container.can_connect():
            # Grab the scrape configs and push a generated config file into the container
            # Parca will automatically reload it's config on changes
            config = self.parca.generate_config(self.profiling_consumer.jobs())
            self.container.push(CONFIG_PATH, str(config), make_dirs=True, permissions=0o644)

            # Remove all store configs on a RemoveStoreEvent, else grab store config from relation
            store_conf = {} if isinstance(event, RemoveStoreEvent) else self.store_requirer.config

            # Add an updated Pebble layer to the container
            # Add a config hash to the layer so if the config changes, replan restarts the service
            layer = self.parca.pebble_layer(self.config, store_conf)
            self.container.add_layer("parca", layer, combine=True)
            self.container.replan()

            self.unit.set_workload_version(self.parca.version)
            self.unit.open_port(protocol="tcp", port=self.parca.port)
            self.unit.status = ops.ActiveStatus()
        else:
            self.unit.status = ops.WaitingStatus("waiting for container")


if __name__ == "__main__":  # pragma: nocover
    ops.main(ParcaOperatorCharm)
