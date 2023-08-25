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
            self, host=f"{self.app.name}.{self.model.name}.svc.cluster.local", port=self.parca.port
        )

        self.parca_store_endpoint = ParcaStoreEndpointProvider(
            charm=self, port=7070, insecure=True
        )

        # Enable the option to send profiles to a remote store (i.e. Polar Signals Cloud)
        self.framework.observe(
            self.on.external_parca_store_endpoint_relation_changed, self._configure_remote_store
        )
        self.framework.observe(
            self.on.external_parca_store_endpoint_relation_broken, self._configure_remote_store
        )

    def _parca_pebble_ready(self, _):
        """Define and start a workload using the Pebble API."""
        self._configure_and_start()

    def _config_changed(self, _):
        """Update the configuration files, restart parca."""
        self._configure_and_start()

    def _update_status(self, _):
        """Handle the update status hook on an interval dictated by model config."""
        self.unit.set_workload_version(self.parca.version)

    def _configure_and_start(self, *, update_static_config=True, remove_store=False):
        """Start Parca having (re)configured it with the relevant jobs."""
        self.unit.status = ops.MaintenanceStatus("reconfiguring parca")

        if self.container.can_connect():
            if update_static_config:
                scrape_configs = self.profiling_consumer.jobs()
                parca_config = self.parca.generate_config(scrape_configs)
                self.container.push(
                    DEFAULT_CONFIG_PATH, str(parca_config), make_dirs=True, permissions=0o644
                )

            # If we're in a relation broken event, ensure we actually remove the store config
            store_config = {} if remove_store else self._remote_store_config

            # Add an updated Pebble layer to the container
            # Add a config hash to the layer so if the config changes, replan restarts the service
            self.container.add_layer(
                "parca",
                self.parca.pebble_layer(self.config, store_config),
                combine=True,
            )

            self.container.replan()

            self.unit.set_workload_version(self.parca.version)
            self.unit.open_port(protocol="tcp", port=self.parca.port)
            self.unit.status = ops.ActiveStatus()
        else:
            self.unit.status = ops.WaitingStatus("waiting for container")

    def _on_profiling_targets_changed(self, _):
        """Update the Parca scrape configuration according to present relations."""
        self.unit.status = ops.MaintenanceStatus("reconfiguring parca")
        self._configure_and_start()
        self.unit.status = ops.ActiveStatus()

    def _configure_remote_store(self, event):
        """Configure store with credentials passed over parca-external-store-endpoint relation."""
        self.unit.status = ops.MaintenanceStatus("reconfiguring parca")
        remove_store = isinstance(event, ops.RelationBrokenEvent)
        self._configure_and_start(update_static_config=False, remove_store=remove_store)
        self.unit.status = ops.ActiveStatus()

    @property
    def _remote_store_config(self) -> dict:
        """Report the remote store config from the external store relation if present."""
        if relation := self.model.get_relation("external-parca-store-endpoint"):
            keys = ["remote-store-address", "remote-store-bearer-token", "remote-store-insecure"]
            return {k: relation.data[relation.app].get(k, "") for k in keys}
        else:
            return {}


if __name__ == "__main__":  # pragma: nocover
    ops.main(ParcaOperatorCharm)
