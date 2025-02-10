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

from models import TLSConfig

logger = logging.getLogger(__name__)

NGINX_DIR = "/etc/nginx"
NGINX_CONFIG = f"{NGINX_DIR}/nginx.conf"
KEY_PATH = f"{NGINX_DIR}/certs/server.key"
CERT_PATH = f"{NGINX_DIR}/certs/server.cert"
RESOLV_CONF_PATH = "/etc/resolv.conf"
CA_CERT_PATH = "/usr/local/share/ca-certificates/ca.cert"

NGINX_PORT = 8080


@dataclasses.dataclass
class Address:
    """Address."""

    name: str
    port: int


class Nginx:
    """Nginx workload."""

    port = NGINX_PORT
    config_path = NGINX_CONFIG

    service_name = "nginx"
    container_name = "nginx"
    layer_name = "nginx"

    def __init__(
            self,
            container: Container,
            server_name: str,
            address: Address,
            http_port: int,
            grpc_port: int,
            path_prefix: Optional[str] = None,
            tls_config: Optional[TLSConfig] = None,
    ):
        self._container = container
        self._server_name = server_name
        self._path_prefix = path_prefix
        self._address = address
        self._http_port = http_port
        self._grpc_port = grpc_port
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
        new_config = NginxConfig(
            self._server_name, tls=self._are_certificates_on_disk, path_prefix=self._path_prefix,
            http_port=self._http_port,
            grpc_port=self._grpc_port,
        ).config(self._address)
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


class NginxConfig:
    """Nginx config builder."""

    def __init__(self, server_name: str, tls: bool,
                 http_port: int,
                 grpc_port: int,
                 path_prefix: Optional[str] = None):
        self._tls = tls
        self._http_port = http_port
        self._grpc_port = grpc_port
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
                    # internally, the parca server listens only to a single (7070 by default) port;
                    # however, we can't generically configure nginx to do proxy_pass AND grpc_pass on the same
                    # external port, so we configure two separate ones that have the same upstream and send
                    # everything to the one parca server.
                    self._build_server_config(self._grpc_port, address.name, self._tls, grpc=True),
                    self._build_server_config(self._http_port, address.name, self._tls),
                ],
            },
        ]
        return full_config

    @staticmethod
    def _log_verbose(verbose: bool = True) -> List[Dict[str, Any]]:
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

    @staticmethod
    def _upstream(address: Address) -> Dict[str, Any]:
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

    def _locations(self, upstream: str, grpc: bool) -> List[Dict[str, Any]]:
        # our locations only use http/grpc (no -secure), because parca doesn't take TLS.
        # nginx has to terminate TLS in both cases and forward all unencrypted to parca.

        protocol = "grpc" if grpc else "http"
        prefix = self._path_prefix  # starts with /

        proxy_block = [
            # upstream server is not running with TLS, so we proxy the request as http
            {"directive": "set", "args": ["$backend", f"{protocol}://{upstream}"]},
            {
                "directive": "grpc_pass" if grpc else "proxy_pass",
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

    @staticmethod
    def _basic_auth(enabled: bool) -> List[Optional[Dict[str, Any]]]:
        if enabled:
            return [
                {"directive": "auth_basic", "args": ['"Tempo"']},
                {
                    "directive": "auth_basic_user_file",
                    "args": ["/etc/nginx/secrets/.htpasswd"],
                },
            ]
        return []

    def _listen(self, port: int, ssl: bool, grpc: bool) -> List[Dict[str, Any]]:
        # listen both on ipv4 and ipv6 to be safe
        directives = [
            {"directive": "listen", "args": self._listen_args(port, False, ssl, grpc=grpc)},
            {"directive": "listen", "args": self._listen_args(port, True, ssl, grpc=grpc)},
        ]
        return directives

    @staticmethod
    def _listen_args(port: int, ipv6: bool, ssl: bool, grpc: bool) -> List[str]:
        args = []
        if ipv6:
            args.append(f"[::]:{port}")
        else:
            args.append(f"{port}")
        if ssl:
            args.append("ssl")
        if grpc:
            args.append("http2")
        return args

    def _build_server_config(self, port: int, upstream: str, tls: bool = False, grpc: bool = False) -> Dict[str, Any]:
        auth_enabled = False

        if tls:
            return {
                "directive": "server",
                "args": [],
                "block": [
                    *self._listen(port, ssl=True, grpc=grpc),
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
                    *self._locations(upstream, grpc=grpc),
                ],
            }

        return {
            "directive": "server",
            "args": [],
            "block": [
                *self._listen(port, ssl=False, grpc=grpc),
                *self._basic_auth(auth_enabled),
                {
                    "directive": "proxy_set_header",
                    "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                },
                {"directive": "server_name", "args": [self.server_name]},
                *self._locations(upstream, grpc=grpc),
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
