#!/usr/bin/env python3
# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Traefik-route ingress configuration."""
import socket
from typing import Dict

from charms.traefik_k8s.v0.traefik_route import TraefikRouteRequirer
from ops import CharmBase


class TraefikRouteEndpoint:
    _endpoint_name =  "ingress"

    # totally arbitrary ports, picked not to collide with tempo's.
    _ports = {
        "parca_grpc": 7993,
        "parca_http": 7994,
    }

    def __init__(self, charm:CharmBase, tls:bool):
        self._charm = charm
        self._tls = tls
        self._app_name = charm.app.name
        self._model_name = charm.model.name
        self._fqdn = socket.getfqdn()
        self._ingress = TraefikRouteRequirer(charm,
                                             charm.model.get_relation(self._endpoint_name),
                                             self._endpoint_name)

    def reconcile(self):
        """Reconcile loop for the ingress configuration."""
        if self._charm.unit.is_leader() and self._ingress.is_ready():
            self._ingress.submit_to_traefik(
                self._ingress_config, static=self._static_ingress_config
            )

    @property
    def _static_ingress_config(self) -> dict:
        entry_points = {}
        for protocol, port in self._ports.items():
            sanitized_protocol = protocol.replace("_", "-")
            entry_points[sanitized_protocol] = {"address": f":{port}"}

        return {"entryPoints": entry_points}

    @property
    def _ingress_config(self) -> dict:
        """Build a raw ingress configuration for Traefik."""
        http_routers = {}
        http_services = {}
        for protocol, port in self._ports.items():
            sanitized_protocol = protocol.replace("_", "-")
            http_routers[f"juju-{self._model_name}-{self._app_name}-{sanitized_protocol}"] = {
                "entryPoints": [sanitized_protocol],
                "service": f"juju-{self._model_name}-{self._app_name}-service-{sanitized_protocol}",
                # TODO better matcher
                "rule": "ClientIP(`0.0.0.0/0`)",
            }
            if (
                protocol == "parca_grpc"
            ) and not self._tls:
                # to send traces to unsecured GRPC endpoints, we need h2c
                # see https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-http-h2c
                http_services[
                    f"juju-{self._model_name}-{self._app_name}-service-{sanitized_protocol}"
                ] = {"loadBalancer": {"servers": [self._build_lb_server_config("h2c", port)]}}
            else:
                # anything else, including secured GRPC, can use _internal_url
                # ref https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-https
                http_services[
                    f"juju-{self._model_name}-{self._app_name}-service-{sanitized_protocol}"
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
