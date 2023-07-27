#!/usr/bin/env python3
# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Parca - a continuous profiling tool."""

import logging

import ops
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.parca.v0.parca_config import (
    DEFAULT_CONFIG_PATH,
    ParcaConfig,
    parca_command_line,
    parse_version,
)
from charms.parca.v0.parca_scrape import ProfilingEndpointConsumer, ProfilingEndpointProvider
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer

logger = logging.getLogger(__name__)


class ParcaOperatorCharm(ops.CharmBase):
    """Charmed Operator to deploy Parca - a continuous profiling tool."""

    _port = 7070

    def __init__(self, *args):
        super().__init__(*args)

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
            self, jobs=[{"static_configs": [{"targets": [f"*:{self._port}"]}]}]
        )

        # The self_profiling_endpoint_provider enables Parca to profile itself
        self.self_profiling_endpoint_provider = ProfilingEndpointProvider(
            self,
            jobs=[{"static_configs": [{"targets": [f"*:{self._port}"]}]}],
            relation_name="self-profiling-endpoint",
        )

        # Allow Parca to provide dashboards to Grafana over a relation
        self._grafana_dashboard_provider = GrafanaDashboardProvider(self)

        self._ingress = IngressPerAppRequirer(
            self, host=f"{self.app.name}.{self.model.name}.svc.cluster.local", port=7070
        )

        self.container = self.unit.get_container("parca")

    def _parca_pebble_ready(self, event):
        """Define and start a workload using the Pebble API."""
        # Get a reference the container attribute on the PebbleReadyEvent
        container = event.workload
        # Configure Parca by writing the config file to the container
        scrape_config = self.profiling_consumer.jobs()
        self._configure(scrape_config, restart=False)

        # Define an initial Pebble layer
        container.add_layer("parca", self._pebble_layer, combine=True)
        container.replan()
        self.unit.set_workload_version(self.version)
        self.unit.open_port(protocol="tcp", port=self._port)
        self.unit.status = ops.ActiveStatus()

    def _update_status(self, _):
        """Handle the update status hook on an interval dictated by model config."""
        self.unit.set_workload_version(self.version)

    def _config_changed(self, _):
        """Update the configuration files, restart parca."""
        self.unit.status = ops.MaintenanceStatus("reconfiguring parca")
        scrape_config = self.profiling_consumer.jobs()

        # Try to configure Parca
        if self.container.can_connect():
            self.container.add_layer("parca", self._pebble_layer, combine=True)
            self._configure(scrape_config)
            self.unit.status = ops.ActiveStatus()
        else:
            self.unit.status = ops.WaitingStatus("waiting for container")

    def _on_profiling_targets_changed(self, _):
        """Update the Parca scrape configuration according to present relations."""
        self.unit.status = ops.MaintenanceStatus("reconfiguring parca")
        self._configure(self.profiling_consumer.jobs())
        self.unit.status = ops.ActiveStatus()

    def _configure(self, scrape_configs=[], *, restart=True):
        """Configure Parca in the container. Restart Parca by default."""
        # Write the config file
        parca_config = ParcaConfig(scrape_configs)
        if self.container.can_connect():
            # TODO(jnsgruk): add user/group details when container is updated
            self.container.push(
                DEFAULT_CONFIG_PATH, str(parca_config), make_dirs=True, permissions=0o644
            )
            if self.container.get_services("parca") and restart:
                self.container.restart("parca")

    @property
    def version(self) -> str:
        """Report the version of Parca."""
        if self.container.can_connect():
            raw_version = self._fetch_version()
            return parse_version(raw_version)
        return ""

    @property
    def _pebble_layer(self) -> ops.pebble.Layer:
        """Return a Pebble layer for Parca based on the current configuration."""
        return ops.pebble.Layer(
            {
                "services": {
                    "parca": {
                        "override": "replace",
                        "summary": "parca",
                        "command": parca_command_line(self.config),
                        "startup": "enabled",
                    }
                },
            }
        )

    def _fetch_version(self):
        """Run parca in the remote container and grab the version."""
        process = self.container.exec(["/parca", "--version"], encoding="utf-8")
        try:
            stdout, _ = process.wait_output()
            return stdout
        except ops.ExecError as e:
            raise e


if __name__ == "__main__":  # pragma: nocover
    ops.main(ParcaOperatorCharm)
