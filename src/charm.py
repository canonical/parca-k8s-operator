#!/usr/bin/env python3
# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Parca - a continuous profiling tool."""

import logging
import socket
from typing import Any, Dict, FrozenSet, List, Optional
from urllib.parse import urlparse

import ops
from charms.catalogue_k8s.v1.catalogue import CatalogueConsumer, CatalogueItem
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.parca_k8s.v0.parca_config import DEFAULT_CONFIG_PATH as CONFIG_PATH
from charms.parca_k8s.v0.parca_scrape import ProfilingEndpointConsumer, ProfilingEndpointProvider
from charms.parca_k8s.v0.parca_store import (
    ParcaStoreEndpointProvider,
    ParcaStoreEndpointRequirer,
    RemoveStoreEvent,
)
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer, charm_tracing_config
from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateRequestAttributes,
    Mode,
    TLSCertificatesRequiresV4,
)
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer

from nginx import (
    CA_CERT_PATH,
    NGINX_PORT,
    NGINX_PROMETHEUS_EXPORTER_PORT,
    Address,
    Nginx,
    NginxPrometheusExporter,
)
from parca import PARCA_PORT, Parca

logger = logging.getLogger(__name__)


@trace_charm(
    tracing_endpoint="_charm_tracing_endpoint",
    server_cert="_server_cert",
    extra_types=[
        Parca,
        ProfilingEndpointConsumer,
        MetricsEndpointProvider,
        ProfilingEndpointProvider,
        GrafanaDashboardProvider,
        ParcaStoreEndpointProvider,
        ParcaStoreEndpointRequirer,
        GrafanaSourceProvider,
        TLSCertificatesRequiresV4,
    ],
)
class ParcaOperatorCharm(ops.CharmBase):
    """Charmed Operator to deploy Parca - a continuous profiling tool."""

    def __init__(self, *args):
        super().__init__(*args)
        self._fqdn = socket.getfqdn()

        self.unit.set_ports(8080)
        self.container = self.unit.get_container("parca")
        self.parca = Parca()
        # The profiling_consumer handles the relation that allows Parca to scrape other apps in the
        # model that provide a "profiling-endpoint" relation.
        self.profiling_consumer = ProfilingEndpointConsumer(self)
        self.framework.observe(
            self.profiling_consumer.on.targets_changed, self._configure_and_start
        )

        #  TLS
        self.certificates = TLSCertificatesRequiresV4(
            charm=self,
            relationship_name="certificates",
            certificate_requests=[self._get_certificate_request_attributes()],
            mode=Mode.UNIT,
        )

        self.ingress = IngressPerAppRequirer(
            self,
            host=self._hostname,
            port=NGINX_PORT,
            scheme=self._scheme,
        )

        # Prometheus scraping config. We scrape the nginx exporter and parca (over nginx)
        self.metrics_endpoint_provider = MetricsEndpointProvider(
            self,
            jobs=self._metrics_scrape_jobs,
            external_url=self._external_url,
            refresh_event=[self.certificates.on.certificate_available],
        )

        # The self_profiling_endpoint_provider enables Parca to profile itself.
        self.self_profiling_endpoint_provider = ProfilingEndpointProvider(
            self,
            jobs=self._format_scrape_target(NGINX_PORT, self._scheme, profiles_path=f"{self._external_url_path or ''}/debug"),
            relation_name="self-profiling-endpoint",
            refresh_event=[self.certificates.on.certificate_available],
        )

        # Allow Parca to provide dashboards to Grafana over a relation.
        self.grafana_dashboard_provider = GrafanaDashboardProvider(self)

        # this needs to be instantiated after `ingress` is
        self.nginx = Nginx(
            container=self.unit.get_container("nginx"),
            server_name=self._hostname,
            address=Address(name="parca", port=PARCA_PORT),
            path_prefix=self._external_url_path,
        )
        self.nginx_exporter = NginxPrometheusExporter(
            container=self.unit.get_container("nginx-prometheus-exporter"),
        )

        self.catalogue = CatalogueConsumer(
            self,
            item=CatalogueItem(
                "Parca UI",
                icon="chart-areaspline",
                url=self._external_url,
                description="""Continuous profiling backend. Allows you to collect, store,
                 query and visualize profiles from your distributed deployment.""",
            ),
        )

        # Enable Parca agents or Parca servers to use this instance as a store.
        self.parca_store_endpoint = ParcaStoreEndpointProvider(
            self,
            port=NGINX_PORT,
            insecure=True,
            external_url=self._external_url,
        )

        # Enable the option to send profiles to a remote store (i.e. Polar Signals Cloud).
        self.store_requirer = ParcaStoreEndpointRequirer(
            self, relation_name="external-parca-store-endpoint"
        )
        # Enable charm tracing
        self.charm_tracing = TracingEndpointRequirer(
            self, relation_name="charm-tracing", protocols=["otlp_http"]
        )
        self._charm_tracing_endpoint, self._server_cert = charm_tracing_config(
            self.charm_tracing, CA_CERT_PATH
        )

        self.grafana_source_provider = GrafanaSourceProvider(
            self,
            source_type="parca",
            source_url=self._external_url,
            refresh_event=[self.certificates.on.certificate_available],
        )

        # conditional logic
        # we must configure and start when pebble-ready or config-changed show up
        self.framework.observe(self.on.parca_pebble_ready, self._configure_and_start)
        self.framework.observe(self.on.config_changed, self._configure_and_start)
        # we may reconfigure if the store config has changed
        self.framework.observe(self.store_requirer.on.endpoints_changed, self._configure_and_start)
        self.framework.observe(self.store_requirer.on.remove_store, self._configure_and_start)
        # we may reconfigure on ingress changes, so that the path-prefix is updated
        self.framework.observe(self.ingress.on.ready, self._configure_and_start)
        self.framework.observe(self.ingress.on.revoked, self._configure_and_start)

        # generic status check
        self.framework.observe(self.on.update_status, self._update_status)

        # unconditional logic
        self._reconcile()

    ##########################
    # === PROPERTIES === #
    ##########################

    @property
    def _app_name(self) -> str:
        """Application name."""
        return self.app.name

    @property
    def _hostname(self) -> str:
        """Unit's hostname."""
        return socket.getfqdn()

    @property
    def _internal_url(self):
        """Return workload's internal URL."""
        return f"{self._scheme}://{self._hostname}:{NGINX_PORT}"

    @property
    def _tls_available(self) -> bool:
        """Return True if tls is enabled and the necessary certs are generated."""
        if not self.model.relations.get("certificates"):
            return False
        cert, key = self.certificates.get_assigned_certificate(
            certificate_request=self._get_certificate_request_attributes()
        )
        return bool(cert and key)

    @property
    def _scheme(self) -> str:
        """Return 'https' if TLS is available else 'http'."""
        return "https" if self._tls_available else "http"

    @property
    def _external_url(self) -> str:
        """Return the external hostname if configured, else the internal one."""
        return self.ingress.url or self._internal_url

    @property
    def _metrics_scrape_jobs(self) -> List[Dict[str, Any]]:
        return self._format_scrape_target(
            NGINX_PROMETHEUS_EXPORTER_PORT,
            # TODO: nginx-prometheus-exporter does not natively run with TLS
            # We can fix that by configuring the nginx container to proxy requests on /nginx-metrics to localhost:9411/metrics
            scheme="http",
        ) + self._format_scrape_target(
            NGINX_PORT,
            scheme=self._scheme,
            metrics_path=f"{self._external_url_path}/metrics" if self.ingress.is_ready else None,
        )

    @property
    def _external_url_path(self) -> Optional[str]:
        """The path part of our external url if we are ingressed, else None.

        This is used to configure the parca server so it can resolve its internal links.
        """
        if not self.ingress.is_ready():
            return None

        external_url = urlparse(self.ingress.url)
        # external_url.path already includes a trailing /
        return str(external_url.path) or None

    def _update_status(self, _):
        """Handle the update status hook on an interval dictated by model config."""
        self.unit.set_workload_version(self.parca.version)

    def _configure_and_start(self, event):
        """Start Parca having (re)configured it with the relevant jobs."""
        # TODO:
        #  - call this method from _reconcile
        #  - remove all observers
        #  - check for config changes by pulling from the container and comparing
        #  - only set maintenance if there are changes
        self.unit.status = ops.MaintenanceStatus("reconfiguring parca")

        if self.container.can_connect():
            # Grab the scrape configs and push a generated config file into the container
            # Parca will automatically reload its config on changes
            config = self.parca.generate_config(self.profiling_consumer.jobs())
            self.container.push(CONFIG_PATH, str(config), make_dirs=True, permissions=0o644)

            # Remove all store configs on a RemoveStoreEvent, else grab store config from relation
            store_conf = {} if isinstance(event, RemoveStoreEvent) else self.store_requirer.config

            # Add an updated Pebble layer to the container
            # Add a config hash to the layer so if the config changes, replan restarts the service
            layer = self.parca.pebble_layer(
                self.config, store_conf, path_prefix=self._external_url_path
            )
            self.container.add_layer("parca", layer, combine=True)
            self.container.replan()

            self.unit.set_workload_version(self.parca.version)
            self.unit.status = ops.ActiveStatus(
                f"UI ready at {self.ingress.url}" if self.ingress.url else ""
            )
        else:
            self.unit.status = ops.WaitingStatus("waiting for container")

    ##########################
    # === UTILITY METHODS === #
    ##########################

    def _reconcile(self):
        """Unconditional logic to run regardless of the event we're processing."""
        self.nginx.configure_pebble_layer()
        self.nginx_exporter.configure_pebble_layer()
        self._configure_nginx_certs()
        # update grafana source and metrics scrape endpoints
        # in case they get changed due to ingress or TLS.
        self.metrics_endpoint_provider.update_scrape_job_spec(self._metrics_scrape_jobs)
        self.grafana_source_provider.update_source(source_url=self._external_url)

    def _configure_nginx_certs(self) -> None:
        """Update the TLS certificates for nginx on disk according to their availability."""
        if not self.container.can_connect():
            return

        if self._tls_available:
            provider_certificate, private_key = self.certificates.get_assigned_certificate(
                certificate_request=self._get_certificate_request_attributes()
            )
            self.nginx.update_certificates(
                provider_certificate.certificate.raw,  # pyright: ignore
                provider_certificate.ca.raw,  # pyright: ignore
                private_key.raw,  # pyright: ignore
            )
        else:
            self.nginx.delete_certificates()

    def _get_certificate_request_attributes(self) -> CertificateRequestAttributes:
        sans_dns: FrozenSet[str] = frozenset([self._hostname])
        return CertificateRequestAttributes(
            # common_name is required and has a limit of 64 chars.
            # it is superseded by sans anyways, so we can use a constrained name,
            # such as app_name
            common_name=self._app_name,
            sans_dns=sans_dns,
        )

    def _format_scrape_target(self, port: int, scheme="http", metrics_path=None, profiles_path=None):
        job: Dict[str, Any] = {"static_configs": [{"targets": [f"{self._hostname}:{port}"]}]}
        if metrics_path:
            job["metrics_path"] = metrics_path
        if profiles_path:
            job["profiling_config"] = {"path_prefix": profiles_path}
        if scheme == "https":
            job["scheme"] = "https"
        return [job]


if __name__ == "__main__":  # pragma: nocover
    ops.main(ParcaOperatorCharm)
