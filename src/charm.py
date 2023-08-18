#!/usr/bin/env python3
# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Parca - a continuous profiling tool."""

import logging

import ops
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.parca.v0.parca_config import DEFAULT_CONFIG_PATH
from charms.parca.v0.parca_scrape import ProfilingEndpointConsumer, ProfilingEndpointProvider
from charms.parca.v0.parca_store import ParcaStoreEndpointProvider
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

        # Observe common Juju events
        self.framework.observe(self.on.parca_pebble_ready, self._parca_pebble_ready)
        self.framework.observe(self.on.config_changed, self._config_changed)
        self.framework.observe(self.on.update_status, self._update_status)

        # The profiling_consumer handles the relation that allows Parca to scrape other apps in the
        # model that provide a "profiling-endpoint" relation
        self.profiling_consumer = ProfilingEndpointConsumer(self)
        self.framework.observe(
            self.profiling_consumer.on.targets_changed, self._on_profiling_targets_changed
        )

        # The metrics_endpoint_provider enables Parca to be scraped by Prometheus for metrics
        self.metrics_endpoint_provider = MetricsEndpointProvider(
            self, jobs=[{"static_configs": [{"targets": [f"*:{self.parca.port}"]}]}]
        )

        # The self_profiling_endpoint_provider enables Parca to profile itself
        self.self_profiling_endpoint_provider = ProfilingEndpointProvider(
            self,
            jobs=[{"static_configs": [{"targets": [f"*:{self.parca.port}"]}]}],
            relation_name="self-profiling-endpoint",
        )

        # Allow Parca to provide dashboards to Grafana over a relation
        self._grafana_dashboard_provider = GrafanaDashboardProvider(self)

        self._ingress = IngressPerAppRequirer(
            self, host=f"{self.app.name}.{self.model.name}.svc.cluster.local", port=7070
        )

        self.parca_store_endpoint = ParcaStoreEndpointProvider(
            charm=self, port=7070, insecure=True
        )

    def _parca_pebble_ready(self, event):
        """Define and start a workload using the Pebble API."""
        # Configure Parca by writing the config file to the container
        self._configure_parca(restart=False)
        # Define an initial Pebble layer
        event.workload.add_layer("parca", self.parca.pebble_layer(self.config), combine=True)
        event.workload.replan()
        self.unit.set_workload_version(self.parca.version)
        self.unit.open_port(protocol="tcp", port=self.parca.port)
        self.unit.status = ops.ActiveStatus()

    def _update_status(self, _):
        """Handle the update status hook on an interval dictated by model config."""
        self.unit.set_workload_version(self.parca.version)

    def _config_changed(self, _):
        """Update the configuration files, restart parca."""
        self.unit.status = ops.MaintenanceStatus("reconfiguring parca")

        # Try to configure Parca
        if self.container.can_connect():
            self.container.add_layer("parca", self.parca.pebble_layer(self.config), combine=True)
            self._configure_parca()
            self.unit.status = ops.ActiveStatus()
        else:
            self.unit.status = ops.WaitingStatus("waiting for container")

    def _on_profiling_targets_changed(self, _):
        """Update the Parca scrape configuration according to present relations."""
        self.unit.status = ops.MaintenanceStatus("reconfiguring parca")
        self._configure_parca()
        self.unit.status = ops.ActiveStatus()

    def _configure_parca(self, *, restart=True):
        """Configure Parca in the container. Restart Parca by default."""
        scrape_configs = self.profiling_consumer.jobs()
        parca_config = self.parca.generate_config(scrape_configs)

        if self.container.can_connect():
            # TODO(jnsgruk): add user/group details when container is updated
            self.container.push(
                DEFAULT_CONFIG_PATH, str(parca_config), make_dirs=True, permissions=0o644
            )
            if self.container.get_services("parca") and restart:
                self.container.restart("parca")


if __name__ == "__main__":  # pragma: nocover
    ops.main(ParcaOperatorCharm)
