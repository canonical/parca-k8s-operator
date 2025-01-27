#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Nginx prometheus exporter workload."""

from ops import Container, pebble

NGINX_PROMETHEUS_EXPORTER_PORT = 9113

_NGINX_PROM_EXPORTER_DIR = "/etc/nginx"
KEY_PATH = f"{_NGINX_PROM_EXPORTER_DIR}/certs/server.key"
CERT_PATH = f"{_NGINX_PROM_EXPORTER_DIR}/certs/server.cert"
CA_CERT_PATH = "/usr/local/share/ca-certificates/ca.cert"


class NginxPrometheusExporter:
    """Nginx prometheus exporter workload."""

    port = NGINX_PROMETHEUS_EXPORTER_PORT
    service_name = "nginx"
    container_name = "nginx-prometheus-exporter"
    layer_name = "nginx-prometheus-exporter"

    def __init__(
        self,
        container: Container,
        nginx_port: int,
    ) -> None:
        self._container = container
        self._nginx_port = nginx_port

    def reconcile(self) -> None:
        """Configure pebble layer and ensure workload is up if possible."""
        if self._container.can_connect():
            self._container.add_layer(self.layer_name, self.layer, combine=True)
            self._container.autostart()

    @property
    def layer(self) -> pebble.Layer:
        """Return the Pebble layer for Nginx Prometheus exporter."""
        # FIXME:
        #
        scrape_uri = "http://127.0.0.1"
        return pebble.Layer(
            {
                "summary": "nginx prometheus exporter layer",
                "description": "pebble config layer for Nginx Prometheus exporter",
                "services": {
                    self.service_name: {
                        "override": "replace",
                        "summary": "nginx prometheus exporter",
                        "command": f"nginx-prometheus-exporter --no-nginx.ssl-verify --web.listen-address=:{self.port}  "
                        f"--nginx.scrape-uri={scrape_uri}:{self._nginx_port}/status",
                        "startup": "enabled",
                    }
                },
            }
        )
