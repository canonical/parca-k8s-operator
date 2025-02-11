#!/usr/bin/env python3
# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Traefik-route ingress configuration."""
import enum
import socket
from collections import namedtuple
from enum import Enum
from typing import Dict, Optional, Sequence

from charms.traefik_k8s.v0.traefik_route import TraefikRouteRequirer
from ops import CharmBase

EntryPoint = namedtuple("Port", "name, protocol, port")

@enum.unique
class Protocol(Enum):
    """Protocols supported by the Parca servers."""
    grpc = "grpc"
    http = "http"


class TraefikRouteEndpoint:
    """Represents a traefik route endpoint and the parca ingress configuration it needs."""
    _endpoint_name = "ingress"

    def __init__(self, charm: CharmBase, tls: bool, entrypoints:Sequence[EntryPoint]):
        self._is_leader = charm.unit.is_leader()
        self._tls = tls
        self._entrypoints = entrypoints
        self._app_name = charm.app.name
        self._model_name = charm.model.name
        self._fqdn = socket.getfqdn()
        self._ingress = TraefikRouteRequirer(
            charm,
            # cfr: https://github.com/canonical/traefik-k8s-operator/issues/448
            charm.model.get_relation(self._endpoint_name), # type: ignore
            self._endpoint_name
        )

    @property
    def is_ready(self) -> bool:
        """Whether traefik_route is ready."""
        return self._ingress.is_ready()

    @property
    def http_external_url(self) -> Optional[str]:
        """The external url, if available."""
        if self._ingress.is_ready() and self._ingress.scheme and self._ingress.external_host:
            return f"{self._ingress.scheme}://{self._ingress.external_host}"

    @property
    def grpc_external_url(self) -> Optional[str]:
        """The external url, if available.

        Will omit the scheme.
        """
        if self._ingress.is_ready() and self._ingress.scheme and self._ingress.external_host:
            return self._ingress.external_host

    def reconcile(self):
        """Reconcile loop for the ingress configuration."""
        if self._is_leader and self._ingress.is_ready():
            self._ingress.submit_to_traefik(
                self._ingress_config, static=self._static_ingress_config
            )

    @property
    def _static_ingress_config(self) -> dict:
        entry_points = {}
        for name, _, port in self._entrypoints:
            entry_points[name] = {"address": f":{port}"}

        return {"entryPoints": entry_points}

    @property
    def _ingress_config(self) -> dict:
        """Build a raw ingress configuration for Traefik."""
        http_routers = {}
        http_services = {}
        for name, protocol, port in self._entrypoints:
            http_routers[f"juju-{self._model_name}-{self._app_name}-{name}"] = {
                "entryPoints": [name],
                "service": f"juju-{self._model_name}-{self._app_name}-service-{name}",
                # TODO better matcher
                "rule": "ClientIP(`0.0.0.0/0`)",
            }
            if protocol is Protocol.grpc and not self._tls:
                # to send traces to unsecured GRPC endpoints, we need h2c
                # see https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-http-h2c
                http_services[
                    f"juju-{self._model_name}-{self._app_name}-service-{name}"
                ] = {"loadBalancer": {"servers": [self._build_lb_server_config("h2c", port)]}}
            else:
                # anything else, including secured GRPC, can use _internal_url
                # ref https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-https
                http_services[
                    f"juju-{self._model_name}-{self._app_name}-service-{name}"
                ] = {"loadBalancer": {"servers": [
                    self._build_lb_server_config("https" if self._tls else "http", port)
                ]}}
        return {
            "http": {
                "routers": http_routers,
                "services": http_services,
            },
        }

    def _build_lb_server_config(self, scheme: str, port: int) -> Dict[str, str]:
        """Build the server portion of the loadbalancer config of Traefik ingress."""
        return {"url": f"{scheme}://{self._fqdn}:{port}"}
