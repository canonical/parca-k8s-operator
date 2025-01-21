#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Nginx workload."""

import dataclasses
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import crossplane
from ops import Container, pebble

logger = logging.getLogger(__name__)

NGINX_DIR = "/etc/nginx"
NGINX_CONFIG = f"{NGINX_DIR}/nginx.conf"
KEY_PATH = f"{NGINX_DIR}/certs/server.key"
CERT_PATH = f"{NGINX_DIR}/certs/server.cert"
RESOLV_CONF_PATH = "/etc/resolv.conf"
CA_CERT_PATH = "/usr/local/share/ca-certificates/ca.cert"

NGINX_PORT = 8080
NGINX_PROMETHEUS_EXPORTER_PORT = 9113


@dataclasses.dataclass
class Address:
    """Address."""

    name: str
    port: int


class Nginx:
    """Nginx workload."""

    port = NGINX_PORT
    _name = "nginx"
    config_path = NGINX_CONFIG

    def __init__(
        self,
        container: Container,
        server_name: str,
        address: Address,
        path_prefix: Optional[str] = None,
    ):
        self._container = container
        self._server_name = server_name
        self._path_prefix = path_prefix
        self._address = address

    @property
    def are_certificates_on_disk(self) -> bool:
        """Return True if the certificates files are on disk."""
        return (
            self._container.can_connect()
            and self._container.exists(CERT_PATH)
            and self._container.exists(KEY_PATH)
            and self._container.exists(CA_CERT_PATH)
        )

    def configure_tls(self, private_key: str, server_cert: str, ca_cert: str) -> None:
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

            # push CA cert to charm container
            Path(CA_CERT_PATH).parent.mkdir(parents=True, exist_ok=True)
            Path(CA_CERT_PATH).write_text(ca_cert)

            # FIXME: uncomment as soon as the nginx image contains the ca-certificates package
            # self._container.exec(["update-ca-certificates", "--fresh"])

    def delete_certificates(self) -> None:
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
            # FIXME: uncomment as soon as the nginx image contains the ca-certificates package
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
            new_config = NginxConfig(
                self._server_name, self.are_certificates_on_disk, path_prefix=self._path_prefix
            ).config(self._address)
            should_restart: bool = self._has_config_changed(new_config)
            self._container.push(self.config_path, new_config, make_dirs=True)  # type: ignore
            self._container.add_layer("nginx", self.layer, combine=True)
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
                    self._name: {
                        "override": "replace",
                        "summary": "nginx",
                        "command": "nginx -g 'daemon off;'",
                        "startup": "enabled",
                    }
                },
            }
        )


class NginxPrometheusExporter:
    """Nginx prometheus exporter."""

    port = NGINX_PROMETHEUS_EXPORTER_PORT

    def __init__(
        self,
        container: Container,
    ) -> None:
        self._container = container

    def reconcile(self) -> None:
        """Configure pebble layer and ensure workload is up if possible."""
        if self._container.can_connect():
            self._container.add_layer("nginx-prometheus-exporter", self.layer, combine=True)
            self._container.autostart()

    @property
    def are_certificates_on_disk(self) -> bool:
        """Return True if the certificates files are on disk."""
        return (
            self._container.can_connect()
            and self._container.exists(CERT_PATH)
            and self._container.exists(KEY_PATH)
            and self._container.exists(CA_CERT_PATH)
        )

    @property
    def layer(self) -> pebble.Layer:
        """Return the Pebble layer for Nginx Prometheus exporter."""
        scheme = "https" if self.are_certificates_on_disk else "http"  # type: ignore
        return pebble.Layer(
            {
                "summary": "nginx prometheus exporter layer",
                "description": "pebble config layer for Nginx Prometheus exporter",
                "services": {
                    "nginx": {
                        "override": "replace",
                        "summary": "nginx prometheus exporter",
                        "command": f"nginx-prometheus-exporter --no-nginx.ssl-verify --web.listen-address=:{self.port}  "
                        f"--nginx.scrape-uri={scheme}://127.0.0.1:{NGINX_PORT}/status",
                        "startup": "enabled",
                    }
                },
            }
        )


class NginxConfig:
    """Nginx config builder."""

    def __init__(self, server_name: str, tls: bool, path_prefix: Optional[str] = None):
        self._tls = tls
        self.server_name = server_name
        self._path_prefix = path_prefix
        self.dns_IP_address = _get_dns_ip_address()

    def config(self, address: Address) -> str:
        """Build and return the Nginx configuration."""
        full_config = self._prepare_config(address)
        return crossplane.build(full_config)

    def _prepare_config(self, address: Address) -> List[dict]:
        log_level = "error"
        # build the complete configuration
        full_config = [
            {"directive": "worker_processes", "args": ["5"]},
            {"directive": "error_log", "args": ["/dev/stderr", log_level]},
            {"directive": "pid", "args": ["/tmp/nginx.pid"]},
            {"directive": "worker_rlimit_nofile", "args": ["8192"]},
            {
                "directive": "events",
                "args": [],
                "block": [{"directive": "worker_connections", "args": ["4096"]}],
            },
            {
                "directive": "http",
                "args": [],
                "block": [
                    # upstreams (load balancing)
                    self._upstream(address),
                    # temp paths
                    {"directive": "client_body_temp_path", "args": ["/tmp/client_temp"]},
                    {"directive": "proxy_temp_path", "args": ["/tmp/proxy_temp_path"]},
                    {"directive": "fastcgi_temp_path", "args": ["/tmp/fastcgi_temp"]},
                    {"directive": "uwsgi_temp_path", "args": ["/tmp/uwsgi_temp"]},
                    {"directive": "scgi_temp_path", "args": ["/tmp/scgi_temp"]},
                    # logging
                    {"directive": "default_type", "args": ["application/octet-stream"]},
                    {
                        "directive": "log_format",
                        "args": [
                            "main",
                            '$remote_addr - $remote_user [$time_local]  $status "$request" $body_bytes_sent "$http_referer" "$http_user_agent" "$http_x_forwarded_for"',
                        ],
                    },
                    *self._log_verbose(verbose=False),
                    # tempo-related
                    {"directive": "sendfile", "args": ["on"]},
                    {"directive": "tcp_nopush", "args": ["on"]},
                    *self._resolver(),
                    {
                        "directive": "map",
                        "args": ["$http_x_scope_orgid", "$ensured_x_scope_orgid"],
                        "block": [
                            {"directive": "default", "args": ["$http_x_scope_orgid"]},
                            {"directive": "", "args": ["anonymous"]},
                        ],
                    },
                    {"directive": "proxy_read_timeout", "args": ["300"]},
                    # server block
                    self._build_server_config(8080, address.name, self._tls),
                ],
            },
        ]
        return full_config

    def _log_verbose(self, verbose: bool = True) -> List[Dict[str, Any]]:
        if verbose:
            return [{"directive": "access_log", "args": ["/dev/stderr", "main"]}]
        return [
            {
                "directive": "map",
                "args": ["$status", "$loggable"],
                "block": [
                    {"directive": "~^[23]", "args": ["0"]},
                    {"directive": "default", "args": ["1"]},
                ],
            },
            {"directive": "access_log", "args": ["/dev/stderr"]},
        ]

    def _upstream(self, address: Address) -> Dict[str, Any]:
        return {
            "directive": "upstream",
            "args": [address.name],
            "block": [
                # TODO: uncomment the below directive when nginx version >= 1.27.3
                # monitor changes of IP addresses and automatically modify the upstream config without the need of restarting nginx.
                # this nginx plus feature has been part of opensource nginx in 1.27.3
                # ref: https://nginx.org/en/docs/http/ngx_http_upstream_module.html#upstream
                # {
                #     "directive": "zone",
                #     "args": [f"{address.name}_zone", "64k"],
                # },
                {
                    "directive": "server",
                    "args": [
                        f"127.0.0.1:{address.port}",
                        # TODO: uncomment the below arg when nginx version >= 1.27.3
                        #  "resolve"
                    ],
                }
            ],
        }

    def _locations(self, upstream: str, tls: bool) -> List[Dict[str, Any]]:
        prefix = self._path_prefix  # starts with /

        protocol = f"http{'s' if tls else ''}"
        proxy_block = [
            {"directive": "set", "args": ["$backend", f"{protocol}://{upstream}"]},
            {
                "directive": "proxy_pass",
                "args": ["$backend"],
            },
            # if a server is down, no need to wait for a long time to pass on
            # the request to the next available one
            {
                "directive": "proxy_connect_timeout",
                "args": ["5s"],
            },
        ]
        redirect_block = [
            {
                "directive": "return",
                "args": ["302", prefix],
            }
        ]
        nginx_locations = [
            {
                "directive": "location",
                "args": ["/"],
                "block": redirect_block if prefix else proxy_block,
            },
        ]
        if prefix:
            nginx_locations.append(
                {
                    "directive": "location",
                    "args": [prefix],
                    "block": proxy_block,
                }
            )
        return nginx_locations

    def _resolver(
        self,
        custom_resolver: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # pass a custom resolver, such as kube-dns.kube-system.svc.cluster.local.
        if custom_resolver:
            return [{"directive": "resolver", "args": [custom_resolver]}]

        # by default, fetch the DNS resolver address from /etc/resolv.conf
        return [
            {
                "directive": "resolver",
                "args": [self.dns_IP_address],
            }
        ]

    def _basic_auth(self, enabled: bool) -> List[Optional[Dict[str, Any]]]:
        if enabled:
            return [
                {"directive": "auth_basic", "args": ['"Tempo"']},
                {
                    "directive": "auth_basic_user_file",
                    "args": ["/etc/nginx/secrets/.htpasswd"],
                },
            ]
        return []

    def _listen(self, port: int, ssl: bool) -> List[Dict[str, Any]]:
        directives = [
            {"directive": "listen", "args": [f"{port}"] + (["ssl"] if ssl else [])},
            {"directive": "listen", "args": [f"[::]:{port}"] + (["ssl"] if ssl else [])},
        ]
        return directives

    def _build_server_config(self, port: int, upstream: str, tls: bool = False) -> Dict[str, Any]:
        auth_enabled = False

        if tls:
            return {
                "directive": "server",
                "args": [],
                "block": [
                    *self._listen(port, ssl=True),
                    *self._basic_auth(auth_enabled),
                    {
                        "directive": "proxy_set_header",
                        "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                    },
                    # FIXME: use a suitable SERVER_NAME
                    {"directive": "server_name", "args": [self.server_name]},
                    {"directive": "ssl_certificate", "args": [CERT_PATH]},
                    {"directive": "ssl_certificate_key", "args": [KEY_PATH]},
                    {"directive": "ssl_protocols", "args": ["TLSv1", "TLSv1.1", "TLSv1.2"]},
                    {"directive": "ssl_ciphers", "args": ["HIGH:!aNULL:!MD5"]},  # codespell:ignore
                    *self._locations(upstream, tls),
                ],
            }

        return {
            "directive": "server",
            "args": [],
            "block": [
                *self._listen(port, ssl=False),
                *self._basic_auth(auth_enabled),
                {
                    "directive": "proxy_set_header",
                    "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                },
                {"directive": "server_name", "args": [self.server_name]},
                *self._locations(upstream, tls),
            ],
        }


def _get_dns_ip_address():
    """Obtain DNS ip address from /etc/resolv.conf."""
    resolv = Path(RESOLV_CONF_PATH).read_text()
    for line in resolv.splitlines():
        if line.startswith("nameserver"):
            # assume there's only one
            return line.split()[1].strip()
    raise RuntimeError("cannot find nameserver in /etc/resolv.conf")
