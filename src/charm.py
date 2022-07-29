#!/usr/bin/env python3
# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Parca - a continuous profiling tool."""

import logging

from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from charms.parca.v0.parca_config import (
    DEFAULT_CONFIG_PATH,
    ParcaConfig,
    parca_command_line,
    parse_version,
)
from charms.prometheus_k8s.v0.prometheus_scrape import (
    MetricsEndpointConsumer,
    MetricsEndpointProvider,
)
from lightkube.models.core_v1 import ServicePort
from ops import pebble
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import Layer

logger = logging.getLogger(__name__)


class ParcaOperatorCharm(CharmBase):
    """Charmed Operator to deploy Parca - a continuous profiling tool."""

    def __init__(self, *args):
        super().__init__(*args)

        # Observe common Juju events
        self.framework.observe(self.on.parca_pebble_ready, self._parca_pebble_ready)
        self.framework.observe(self.on.config_changed, self._config_changed)
        self.framework.observe(self.on.update_status, self._update_status)

        # Patch the Kubernetes service to contain the correct port
        port = ServicePort(7070, name=f"{self.app.name}")
        self.service_patcher = KubernetesServicePatch(self, [port])

        # The profiling_consumer handles the relation that allows Parca to scrape other apps in the
        # model that provide a "profiling-endpoint" relation
        self.profiling_consumer = MetricsEndpointConsumer(self, relation_name="profiling-endpoint")
        self.framework.observe(
            self.profiling_consumer.on.targets_changed, self._on_profiling_targets_changed
        )

        # The metrics_endpoint_provider enables Parca to be scraped by Prometheus for metrics
        self.metrics_endpoint_provider = MetricsEndpointProvider(
            self,
            jobs=[{"static_configs": [{"targets": ["*:7070"]}]}],
            relation_name="metrics-endpoint",
        )

        # The self_profiling_endpoint_provider enables Parca to profile itself
        self.self_profiling_endpoint_provider = MetricsEndpointProvider(
            self,
            jobs=[{"static_configs": [{"targets": ["*:7070"]}]}],
            relation_name="self-profiling-endpoint",
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
        self.unit.status = ActiveStatus()

    def _update_status(self, _):
        """Performed on an interval dictated by model config."""
        self.unit.set_workload_version(self.version)

    def _config_changed(self, _):
        """Update the configuration files, restart parca."""
        self.unit.status = MaintenanceStatus("reconfiguring parca")
        scrape_config = self.profiling_consumer.jobs()

        # Try to configure Parca
        if self.container.can_connect():
            self._configure(scrape_config)
            self.unit.status = ActiveStatus()
        else:
            self.unit.status = WaitingStatus("waiting for container")

    def _on_profiling_targets_changed(self, _):
        """Update the Parca scrape configuration according to present relations."""
        self.unit.status = MaintenanceStatus("reconfiguring parca")
        self._configure(self.profiling_consumer.jobs())
        self.unit.status = ActiveStatus()

    def _configure(self, scrape_configs=[], *, restart=True):
        """Configure Parca in the container. Restart Parca by default."""
        # Write the config file
        parca_config = ParcaConfig(scrape_configs)
        if self.container.can_connect():
            # TODO(jnsgruk): add user/group details when container is updated
            self.container.push(
                DEFAULT_CONFIG_PATH, str(parca_config), make_dirs=True, permissions=0o644
            )
            if restart:
                self.container.restart("parca")

    @property
    def version(self) -> str:
        """Reports the version of Parca."""
        if self.container.can_connect():
            raw_version = self._fetch_version()
            return parse_version(raw_version)
        return ""

    @property
    def _pebble_layer(self) -> Layer:
        """Returns a Pebble layer for Parca based on the current configuration."""
        return Layer(
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
        except pebble.ExecError as e:
            raise e


if __name__ == "__main__":  # pragma: nocover
    main(ParcaOperatorCharm)
