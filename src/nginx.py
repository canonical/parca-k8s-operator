#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Nginx workload."""

import dataclasses
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

from coordinated_workers.nginx import NginxConfig, NginxLocationConfig, NginxUpstream
from ops import Container, pebble

from models import TLSConfig

logger = logging.getLogger(__name__)

NGINX_DIR = "/etc/nginx"
NGINX_CONFIG = f"{NGINX_DIR}/nginx.conf"
KEY_PATH = f"{NGINX_DIR}/certs/server.key"
CERT_PATH = f"{NGINX_DIR}/certs/server.cert"
RESOLV_CONF_PATH = "/etc/resolv.conf"
CA_CERT_PATH = "/usr/local/share/ca-certificates/ca.cert"


@dataclasses.dataclass
class Address:
    """Address."""

    name: str
    port: int


class Nginx:
    """Nginx workload."""

    # totally arbitrary ports, picked not to collide with tempo's.
    parca_grpc_server_port = 7993
    parca_http_server_port = 7994

    # port for the upstream
    config_path = NGINX_CONFIG

    service_name = "nginx"
    container_name = "nginx"
    layer_name = "nginx"

    def __init__(
        self,
        container: Container,
        server_name: str,
        address: Address,
        tls_config: Optional[TLSConfig] = None,
    ):
        self._container = container
        self._server_name = server_name
        self._address = address
        self._tls_config = tls_config

    @property
    def _are_certificates_on_disk(self) -> bool:
        """Return True if the certificates files are on disk."""
        return (
            self._container.can_connect()
            and self._container.exists(CERT_PATH)
            and self._container.exists(KEY_PATH)
            and self._container.exists(CA_CERT_PATH)
        )

    def _update_certificates(self, server_cert: str, ca_cert: str, private_key: str) -> None:
        """Save the certificates file to disk and run update-ca-certificates."""
        if self._container.can_connect():
            # Read the current content of the files (if they exist)
            current_server_cert = (
                self._container.pull(CERT_PATH).read() if self._container.exists(CERT_PATH) else ""
            )
            current_private_key = (
                self._container.pull(KEY_PATH).read() if self._container.exists(KEY_PATH) else ""
            )
            current_ca_cert = (
                self._container.pull(CA_CERT_PATH).read()
                if self._container.exists(CA_CERT_PATH)
                else ""
            )
            if (
                current_server_cert == server_cert
                and current_private_key == private_key
                and current_ca_cert == ca_cert
            ):
                # No update needed
                return

            self._container.push(KEY_PATH, private_key, make_dirs=True)
            self._container.push(CERT_PATH, server_cert, make_dirs=True)
            self._container.push(CA_CERT_PATH, ca_cert, make_dirs=True)

        # TODO: uncomment when nginx container has update-ca-certificates command
        # self._container.exec(["update-ca-certificates", "--fresh"])

    def _delete_certificates(self) -> None:
        """Delete the certificate files from disk and run update-ca-certificates."""
        if self._container.can_connect():
            if self._container.exists(CERT_PATH):
                self._container.remove_path(CERT_PATH, recursive=True)
            if self._container.exists(KEY_PATH):
                self._container.remove_path(KEY_PATH, recursive=True)
            if self._container.exists(CA_CERT_PATH):
                self._container.remove_path(CA_CERT_PATH, recursive=True)
            if Path(CA_CERT_PATH).exists():
                Path(CA_CERT_PATH).unlink(missing_ok=True)

            # TODO: uncomment when nginx container has update-ca-certificates command
            # self._container.exec(["update-ca-certificates", "--fresh"])

    def _has_config_changed(self, new_config: str) -> bool:
        """Return True if the passed config differs from the one on disk."""
        if not self._container.can_connect():
            logger.debug("Could not connect to Nginx container")
            return False

        try:
            current_config = self._container.pull(self.config_path).read()
        except (pebble.ProtocolError, pebble.PathError) as e:
            logger.warning(
                "Could not check the current nginx configuration due to "
                "a failure in retrieving the file: %s",
                e,
            )
            return False

        return current_config != new_config

    def reload(self) -> None:
        """Reload the nginx config without restarting the service."""
        if self._container.can_connect():
            self._container.exec(["nginx", "-s", "reload"])

    def reconcile(self) -> None:
        """Configure pebble layer and ensure workload is up if possible."""
        if self._container.can_connect():
            # keep the reconcile_tls_config call on top: otherwise on certificates-broken,
            # _are_certificates_on_disk will still return True and nginx will be configured with tls on.
            # and vice versa, on certificates-created, _are_certificates_on_disk will still return False
            # for a while because we haven't written the certs to disk yet, and we'll start nginx
            # without tls config.
            self._reconcile_tls_config()
            self._reconcile_nginx_config()

    def _reconcile_tls_config(self):
        tls_config = self._tls_config
        if tls_config:
            self._update_certificates(
                tls_config.certificate.certificate.raw,  # pyright: ignore
                tls_config.certificate.ca.raw,  # pyright: ignore
                tls_config.key.raw,  # pyright: ignore
            )
        else:
            self._delete_certificates()

    def _reconcile_nginx_config(self):
        configbuilder = NginxConfig(
            self._server_name,
            upstream_configs=self._nginx_upstreams(),
            server_ports_to_locations=self._server_ports_to_locations(),
        )
        new_config = configbuilder.get_config(
            upstreams_to_addresses=self._upstreams_to_addresses(),
                     listen_tls=self._are_certificates_on_disk
        )

        should_restart: bool = self._has_config_changed(new_config)
        self._container.push(self.config_path, new_config, make_dirs=True)  # type: ignore
        self._container.add_layer(self.layer_name, self.layer, combine=True)
        self._container.autostart()

        if should_restart:
            logger.info("new nginx config: restarting the service")
            self.reload()

    @property
    def layer(self) -> pebble.Layer:
        """Return the Pebble layer for Nginx."""
        return pebble.Layer(
            {
                "summary": "Nginx layer",
                "description": "Pebble config layer for Nginx",
                "services": {
                    self.service_name: {
                        "override": "replace",
                        "summary": "nginx",
                        "command": "nginx -g 'daemon off;'",
                        "startup": "enabled",
                    }
                },
            }
        )

    def _nginx_upstreams(self) -> List[NginxUpstream]:
        return [
            NginxUpstream(name=self._address.name, port=self._address.port,worker_role=self._address.name)
        ]

    def _server_ports_to_locations(self) -> Dict[int, List[NginxLocationConfig]]:
        # We pass upstream_tls=False to proxy requests using plain HTTP since parca's upstream server doesn't support TLS
        return {
            self.parca_grpc_server_port: [
                NginxLocationConfig(path="/", backend=self._address.name, is_grpc=True, upstream_tls=False)
            ],
            self.parca_http_server_port: [
                NginxLocationConfig(path="/", backend=self._address.name, upstream_tls=False)
            ]
        }
    def _upstreams_to_addresses(self) -> Dict[str, Set[str]]:
        return {
            self._address.name: {"127.0.0.1"}
        }
