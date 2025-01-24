#!/usr/bin/env python3
# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Parca - a continuous profiling tool."""

import logging
import socket
import typing
from typing import FrozenSet, List, Optional
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional
from urllib.parse import urlparse

import ops
from charms.catalogue_k8s.v1.catalogue import CatalogueConsumer, CatalogueItem
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.parca_k8s.v0.parca_scrape import ProfilingEndpointConsumer, ProfilingEndpointProvider
from charms.parca_k8s.v0.parca_store import (
    ParcaStoreEndpointProvider,
    ParcaStoreEndpointRequirer,
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
from parca import PARCA_PORT, Parca, ScrapeJob, ScrapeJobsConfig

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

        # ENDPOINT WRAPPERS
        self.profiling_consumer = ProfilingEndpointConsumer(self)
        self.certificates = TLSCertificatesRequiresV4(
            charm=self,
            relationship_name="certificates",
            certificate_requests=[self._get_certificate_request_attributes()],
            mode=Mode.UNIT,
        )
        self.ingress = IngressPerAppRequirer(
            self,
            host=self._fqdn,
            port=NGINX_PORT,
            scheme=self._scheme,
        )
        self.metrics_endpoint_provider = MetricsEndpointProvider(
            self,
            jobs=self._metrics_scrape_jobs,
            external_url=self._external_url,
            refresh_event=[self.certificates.on.certificate_available],
        )
        self.self_profiling_endpoint_provider = ProfilingEndpointProvider(
            self,
            jobs=self._self_profiling_scrape_jobs,
            relation_name="self-profiling-endpoint",
            refresh_event=[self.certificates.on.certificate_available],
        )
        self.grafana_dashboard_provider = GrafanaDashboardProvider(self)

        # this needs to be instantiated after `ingress` is
        self.nginx = Nginx(
            container=self.unit.get_container("nginx"),
            server_name=self._fqdn,
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
        self.parca_store_endpoint = ParcaStoreEndpointProvider(
            self,
            port=NGINX_PORT,
            insecure=True,
            external_url=self._external_url,
        )
        self.store_requirer = ParcaStoreEndpointRequirer(
            self, relation_name="external-parca-store-endpoint"
        )
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

        # WORKLOADS
        # these need to be instantiated after `ingress` is, as it accesses self._external_url_path
        self.parca = Parca(
            container=self.unit.get_container("parca"),
            scrape_configs=self.profiling_consumer.jobs(),
            enable_persistence=typing.cast(bool, self.config.get("enable-persistence", None)),
            memory_storage_limit=typing.cast(int, self.config.get("memory-storage-limit", None)),
            store_config=self.store_requirer.config,
            path_prefix=self._external_url_path,
        )
        self.nginx_exporter = NginxPrometheusExporter(
            container=self.unit.get_container("nginx-prometheus-exporter"),
        )
        self.nginx = Nginx(
            container=self.unit.get_container("nginx"),
            server_name=self._fqdn,
            address=Address(name="parca", port=PARCA_PORT),
            path_prefix=self._external_url_path,
        )

        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)

        # unconditional logic
        self._reconcile()

    ##########################
    # === PROPERTIES === #
    ##########################

    @property
    def _internal_url(self):
        """Return workload's internal URL."""
        return f"{self._scheme}://{self._fqdn}:{NGINX_PORT}"

    @property
    def _tls_ready(self) -> bool:
        """Return True if tls is enabled and the necessary certs are generated."""
        if not self.model.relations.get("certificates"):
            return False

        return all(
            self.certificates.get_assigned_certificate(
                certificate_request=self._get_certificate_request_attributes()
            )
        )

    @property
    def _scheme(self) -> str:
        """Return 'https' if TLS is available else 'http'."""
        return "https" if self._tls_ready else "http"

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
            metrics_path=f"{self._external_url_path or ''}/metrics",
        )

    def _reconcile(self):
        """Unconditional logic to run regardless of the event we're processing.

        This will ensure all workloads are up and running if the preconditions are met.
        """
        self.nginx.reconcile()
        self.nginx_exporter.reconcile()
        self.parca.reconcile()

        self._configure_certs()
        # update grafana source, metrics scrape, and profiling scrape endpoints
        # in case they get changed due to ingress or TLS.
        self.metrics_endpoint_provider.update_scrape_job_spec(self._metrics_scrape_jobs)
        self.self_profiling_endpoint_provider.update_scrape_job_spec(
            self._self_profiling_scrape_jobs
        )
        self.grafana_source_provider.update_source(source_url=self._external_url)


    def _self_profiling_scrape_jobs(self) -> List[Dict[str, Any]]:
        return self._format_scrape_target(
            NGINX_PORT, self._scheme, profiles_path=self._external_url_path
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

    def _on_collect_unit_status(self, event: ops.CollectStatusEvent):
        """Set unit status depending on the state."""
        containers_not_ready = [
            c_name
            for c_name in {"parca", "nginx", "nginx-prometheus-exporter"}
            if not self.unit.get_container(c_name).can_connect()
        ]

        if containers_not_ready:
            event.add_status(
                ops.WaitingStatus(f"Waiting for containers: {containers_not_ready}...")
            )
        else:
            self.unit.set_workload_version(self.parca.version)

        event.add_status(ops.ActiveStatus(f"UI ready at {self._external_url}"))

    ##########################
    # === UTILITY METHODS === #
    ##########################
    def _configure_certs(self) -> None:
        """Update the TLS certificates for nginx/parca/charm containers on disk according to their availability."""
        if self._tls_ready:
            provider_certificate, private_key = self.certificates.get_assigned_certificate(
                certificate_request=self._get_certificate_request_attributes()
            )
            self.nginx.update_certificates(
                provider_certificate.certificate.raw,  # pyright: ignore
                provider_certificate.ca.raw,  # pyright: ignore
                private_key.raw,  # pyright: ignore
            )
            # parca container needs the CA certificate when scraping https profiling endpoints
            self.parca.update_ca_certificate(provider_certificate.ca.raw)  # pyright: ignore
        else:
            self.nginx.delete_certificates()
            self.parca.delete_ca_certificate()

    def _get_certificate_request_attributes(self) -> CertificateRequestAttributes:
        sans_dns: FrozenSet[str] = frozenset([self._fqdn])
        return CertificateRequestAttributes(
            # common_name is required and has a limit of 64 chars.
            # it is superseded by sans anyway, so we can use a constrained name,
            # such as app_name
            common_name=self._app_name,
            sans_dns=sans_dns,
        )

    def _format_scrape_target(
        self, port: int, scheme="http", metrics_path=None, profiles_path=None
    )-> List[ScrapeJobsConfig]:
        job: ScrapeJob = {"targets": [f"{self._fqdn}:{port}"]}
        jobsconfig: ScrapeJobsConfig = {"static_configs": [job]}
        if metrics_path:
            jobsconfig["metrics_path"] = metrics_path
        if profiles_path:
            jobsconfig["profiling_config"] = {"path_prefix": profiles_path}
        if scheme == "https":
            jobsconfig["scheme"] = "https"
            if Path(CA_CERT_PATH).exists():
                jobsconfig["tls_config"] = {
                    # ca_file should hold the CA path, but prometheus charm expects ca_file to hold the cert contents.
                    # https://github.com/canonical/prometheus-k8s-operator/issues/670
                    "ca_file" if metrics_path else "ca": Path(CA_CERT_PATH).read_text()
                }

        return [jobsconfig]


if __name__ == "__main__":  # pragma: nocover
    ops.main(ParcaOperatorCharm)
