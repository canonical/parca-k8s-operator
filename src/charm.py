#!/usr/bin/env python3
# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Parca - a continuous profiling tool."""

import logging
import socket
import typing
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional

import ops
import ops_tracing
import pydantic
from charmlibs.interfaces.slo import SLOProvider
from charms.catalogue_k8s.v1.catalogue import CatalogueConsumer, CatalogueItem
from charms.data_platform_libs.v0.s3 import S3Requirer
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.parca_k8s.v0.parca_scrape import ProfilingEndpointConsumer, ProfilingEndpointProvider
from charms.parca_k8s.v0.parca_store import (
    ParcaStoreEndpointProvider,
    ParcaStoreEndpointRequirer,
)
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer
from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateRequestAttributes,
    Mode,
    TLSCertificatesRequiresV4,
)
from cosl import JujuTopology

from ingress_configuration import EntryPoint, Protocol, TraefikRouteEndpoint
from models import S3Config, TLSConfig
from nginx import (
    Address,
    Nginx,
)
from nginx_prometheus_exporter import NginxPrometheusExporter
from parca import Parca, RelabelConfig, ScrapeJob, ScrapeJobsConfig

logger = logging.getLogger(__name__)

# where we store the certificate in the charm container
CA_CERT_PATH = "/usr/local/share/ca-certificates/ca.cert"

CERTIFICATES_RELATION_NAME = "certificates"
PARCA_CONTAINER = "parca"
NGINX_CONTAINER = "nginx"

# we can ask s3 for a bucket name, but we may get back a different one
PREFERRED_BUCKET_NAME = "parca"
RELABEL_CONFIG = [
    {
        "source_labels": [
            "juju_model",
            "juju_model_uuid",
            "juju_application",
            "juju_unit",
        ],
        "separator": "_",
        "target_label": "instance",
        "regex": "(.*)",
    }
]


class ParcaOperatorCharm(ops.CharmBase):
    """Charmed Operator to deploy Parca - a continuous profiling tool."""

    def __init__(self, *args):
        super().__init__(*args)
        self._fqdn = socket.getfqdn()

        # ENDPOINT WRAPPERS
        self.profiling_consumer = ProfilingEndpointConsumer(self)
        self.certificates = TLSCertificatesRequiresV4(
            charm=self,
            relationship_name=CERTIFICATES_RELATION_NAME,
            certificate_requests=[self._get_certificate_request_attributes()],
            mode=Mode.UNIT,
        )
        self.ingress = TraefikRouteEndpoint(
            self,
            tls=self._tls_ready,
            entrypoints=(
                EntryPoint("parca-grpc", Protocol.grpc, Nginx.parca_grpc_server_port),
                EntryPoint("parca-http", Protocol.http, Nginx.parca_http_server_port),
            ),
        )
        self.metrics_endpoint_provider = MetricsEndpointProvider(
            self,
            jobs=self._metrics_scrape_jobs,
            external_url=self.http_server_url,
            refresh_event=[self.certificates.on.certificate_available],
        )

        self.self_profiling_endpoint_provider = ProfilingEndpointProvider(
            self,
            jobs=self._self_profiling_scrape_jobs,
            relation_name="self-profiling-endpoint",
            refresh_event=[self.certificates.on.certificate_available],
        )
        self.grafana_dashboard_provider = GrafanaDashboardProvider(self)
        self.s3_requirer = S3Requirer(self, "s3", bucket_name=PREFERRED_BUCKET_NAME)
        self.logging = LogForwarder(self)

        self.catalogue = CatalogueConsumer(
            self,
            item=CatalogueItem(
                "Parca UI",
                icon="chart-areaspline",
                url=self.http_server_url,
                description="""Continuous profiling backend. Allows you to collect, store,
                 query and visualize profiles from your distributed deployment.""",
            ),
        )
        self.parca_store_endpoint = ParcaStoreEndpointProvider(
            self,
            port=Nginx.parca_grpc_server_port,
            external_url=self.grpc_server_url,
            insecure=(self._scheme == "http"),
        )
        self.store_requirer = ParcaStoreEndpointRequirer(
            self, relation_name="external-parca-store-endpoint"
        )
        self.charm_tracing = TracingEndpointRequirer(
            self, relation_name="charm-tracing", protocols=["otlp_http"]
        )
        self.grafana_source_provider = GrafanaSourceProvider(
            self,
            source_type="parca",
            source_url=self.http_server_url,
            # no need to use refresh_events logic as we refresh on reconcile.
        )

        self.slo_provider = SLOProvider(self, relation_name="slos")

        self.workload_tracing = TracingEndpointRequirer(
            self,
            relation_name="workload-tracing",
            protocols=["otlp_grpc"],
        )

        # WORKLOADS
        # these need to be instantiated after `ingress` is, as it accesses self._external_url_path
        self.nginx = Nginx(
            container=self.unit.get_container(Nginx.container_name),
            server_name=self._fqdn,
            address=Address(name="parca", port=Parca.port),
            tls_config=self._tls_config,
        )
        self.parca = Parca(
            container=self.unit.get_container(Parca.container_name),
            scrape_configs=self._profiling_scrape_configs,
            enable_persistence=typing.cast(bool, self.config.get("enable-persistence", None)),
            memory_storage_limit=typing.cast(int, self.config.get("memory-storage-limit", None)),
            store_config=self.store_requirer.config,
            tls_config=self._tls_config,
            s3_config=self._s3_config,
            tracing_endpoint=self._workload_tracing_endpoint,
        )
        self.nginx_exporter = NginxPrometheusExporter(
            container=self.unit.get_container(NginxPrometheusExporter.container_name),
            nginx_port=Nginx.parca_http_server_port,
        )

        # event handlers
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)

        # keep this after the collect-status observer, but before any other event handler
        if self.is_scaled_up():
            logger.error("Application has scale >1 but doesn't support scaling. "
                         "Deploy a new application instead.")
            return

        self.framework.observe(self.on.list_endpoints_action, self._on_list_endpoints_action)
        self.framework.observe(self.on.get_slo_template_action, self._on_get_slo_template_action)
        # unconditional logic
        self.reconcile()

    def is_scaled_up(self)->bool:
        """Check whether we have peers."""
        peer_relation = self.model.get_relation("parca-peers")
        if not peer_relation:
            return False
        return len(peer_relation.units) > 0

    # RECONCILERS
    def reconcile(self):
        """Unconditional logic to run regardless of the event we are processing.

        This will ensure all workloads are up and running if the preconditions are met.
        """
        self.unit.set_ports(Nginx.parca_http_server_port, Nginx.parca_grpc_server_port)
        if self.charm_tracing.is_ready() and (endpoint:= self.charm_tracing.get_endpoint("otlp_http")):
            ops_tracing.set_destination(
                url=endpoint + "/v1/traces",
                ca=self._tls_config.certificate.ca.raw if self._tls_config else None
            )

        self.nginx.reconcile()
        self.nginx_exporter.reconcile()
        self.parca.reconcile()

        self._reconcile_tls_config()
        self._reconcile_relations()

    def _reconcile_relations(self):
        # update all outgoing relation data
        # in case they changed e.g. due to ingress or TLS config changes
        # we do this on each event instead of relying on the libs' own refresh_event
        # mechanism to ensure we don't miss any events. This data should always be up to date,
        # and it's a cheap operation to push it, so we always do it.
        self.metrics_endpoint_provider.set_scrape_job_spec()
        self.self_profiling_endpoint_provider.set_scrape_job_spec()
        self.grafana_source_provider.update_source(source_url=self.http_server_url)
        self.parca_store_endpoint.set_remote_store_connection_data()
        self.ingress.reconcile()
        self._provide_slos()

    def _reconcile_tls_config(self) -> None:
        """Update the TLS certificates for the charm container."""
        # push CA cert to charm container
        cacert_path = Path(CA_CERT_PATH)
        if tls_config := self._tls_config:
            cacert_path.parent.mkdir(parents=True, exist_ok=True)
            cacert_path.write_text(tls_config.certificate.ca.raw)
        else:
            cacert_path.unlink(missing_ok=True)

    # INGRESS/ROUTING PROPERTIES
    @property
    def http_server_url(self):
        """Http server url; ingressed if available, else over fqdn."""
        if external_host := self.ingress.http_external_host:
            # this already includes the scheme: http or https, depending on the ingress
            return f"{external_host}:{Nginx.parca_http_server_port}"
        return f"{self._internal_scheme}://{self._fqdn}:{Nginx.parca_http_server_port}"

    @property
    def grpc_server_url(self):
        """Grpc server url; ingressed if available, else over fqdn.

        It will NOT include the scheme.
        """
        if external_host := self.ingress.grpc_external_host:
            # this does not include any scheme.
            return f"{external_host}:{Nginx.parca_grpc_server_port}"
        return f"{self._fqdn}:{Nginx.parca_grpc_server_port}"

    @property
    def _scheme(self):
        """Return ingress scheme if available, else return the internal scheme."""
        return self.ingress.scheme or self._internal_scheme

    @property
    def _internal_scheme(self) -> str:
        """Return 'https' if TLS is available else 'http'."""
        return "https" if self._tls_ready else "http"

    # TLS CONFIG
    @property
    def _tls_config(self) -> Optional["TLSConfig"]:
        if not self.model.relations.get(CERTIFICATES_RELATION_NAME):
            return None
        cr = self._get_certificate_request_attributes()
        certificate, key = self.certificates.get_assigned_certificate(certificate_request=cr)

        if not (key and certificate):
            return None
        return TLSConfig(cr, key=key, certificate=certificate)

    @property
    def _tls_ready(self) -> bool:
        """Return True if tls is enabled and the necessary data is available."""
        return bool(self._tls_config)

    def _get_certificate_request_attributes(self) -> CertificateRequestAttributes:
        sans_dns: FrozenSet[str] = frozenset([self._fqdn])
        return CertificateRequestAttributes(
            # common_name is required and has a limit of 64 chars.
            # it is superseded by sans anyway, so we can use a constrained name,
            # such as app_name
            common_name=self.app.name,
            sans_dns=sans_dns,
        )

    # STORAGE CONFIG
    @property
    def _s3_config(self) -> Optional[S3Config]:
        """Cast and validate the untyped s3 databag to something we can handle."""
        try:
            # we have to type-ignore here because the s3 lib's type annotation is wrong
            raw = self.s3_requirer.get_s3_connection_info()
            return S3Config(**raw)  # type: ignore
        except pydantic.ValidationError:
            logger.debug("s3 connection absent or corrupt")
            return None

    # PROFILING SCRAPE JOBS CONFIGURATION
    @property
    def _profiling_scrape_configs(self) -> List[ScrapeJobsConfig]:
        """The scrape configuration that Parca will use for scraping profiles.

        The configuration includes the targets scraped by Parca as well as Parca's
        own workload profiles if they are not already being scraped by a remote Parca.
        """
        scrape_configs = self.profiling_consumer.jobs()
        # Append parca's self scrape config if no remote parca instance is integrated over "self-profiling-endpoint"
        if not self.self_profiling_endpoint_provider.is_ready():
            scrape_configs.append(self._self_profiling_scrape_config)
        return scrape_configs

    @property
    def _self_profiling_scrape_jobs(self) -> List[ScrapeJobsConfig]:
        """The self-profiling scrape jobs that will become other parca's scrape configs."""
        # we use a wildcard hostname here so that ProfilingEndpointConsumer._labeled_static_job_config
        # does not miscategorize this job as "unitless".
        # wildcard means: scrape all units of this application on this port.
        # If we omit it (and use self._fqdn) instead, it will create two jobs, one that will
        # scrape the right endpoint but assign it the wrong labels, and the other one
        # with the right labels but scraping a nonexisting endpoint.
        return self._parca_scrape_target(fqdn="*")

    @property
    def _self_profiling_scrape_config(self) -> ScrapeJobsConfig:
        """Profiling scrape config to scrape parca's own workload profiles.

        This config also adds juju topology to the scraped profiles.
        """
        job_name = "parca"
        # add the juju_ prefix to labels
        labels = {
            "juju_{}".format(key): value
            for key, value in JujuTopology.from_charm(self).as_dict().items()
            if value
        }

        return self._parca_scrape_target(
            labels=labels,
            job_name=job_name,
            relabel_configs=RELABEL_CONFIG,
        )[0]

    @property
    def _metrics_scrape_jobs(self) -> List[ScrapeJobsConfig]:
        return self._prometheus_scrape_target(
            NginxPrometheusExporter.port,
            # FIXME: https://github.com/canonical/parca-k8s-operator/issues/399
            #  nginx-prometheus-exporter does not natively run with TLS
            #  We can fix that by configuring the nginx container to proxy requests on
            #  /nginx-metrics to localhost:9411/metrics
            #  so once we relate with SSC, will metrics scraping be broken?
            scheme="http",
        ) + self._prometheus_scrape_target(
            Nginx.parca_http_server_port,
            scheme=self._internal_scheme,
        )

    def _parca_scrape_target(self, **kwargs):
        return _generic_scrape_target(
            fqdn=kwargs.pop("fqdn", self._fqdn),
            port=Nginx.parca_http_server_port,
            scheme=self._internal_scheme,
            tls_config_ca_file_key="ca",
            **kwargs,
        )

    def _prometheus_scrape_target(self, port: int, **kwargs):
        # ca_file should hold the CA path, but prometheus charm expects ca_file to hold the cert contents.
        # https://github.com/canonical/prometheus-k8s-operator/issues/670
        return _generic_scrape_target(
            fqdn=self._fqdn, port=port, tls_config_ca_file_key="ca_file", **kwargs
        )

    # TRACING PROPERTIES
    @property
    def _workload_tracing_endpoint(self) -> Optional[str]:
        if self.workload_tracing.is_ready():
            endpoint = self.workload_tracing.get_endpoint("otlp_grpc")
            return endpoint
        return None

    # EVENT HANDLERS
    def _on_collect_unit_status(self, event: ops.CollectStatusEvent):
        """Set unit status depending on the state."""
        if self.is_scaled_up():
            event.add_status(ops.BlockedStatus("You can't scale up parca-k8s. Deploy a new application instead."))

        containers_not_ready = [
            workload.container_name
            for workload in {Parca, Nginx, NginxPrometheusExporter}
            if not self.unit.get_container(workload.container_name).can_connect()
        ]

        if containers_not_ready:
            event.add_status(
                ops.WaitingStatus(f"Waiting for containers: {containers_not_ready}...")
            )
        else:
            self.unit.set_workload_version(self.parca.version)

        event.add_status(ops.ActiveStatus(f"UI ready at {self.http_server_url}"))

    def _on_list_endpoints_action(self, event: ops.ActionEvent):
        """React to the list-endpoints action."""
        out = {
            "direct-http-url": f"{self._scheme}://{self._fqdn}:{Nginx.parca_http_server_port}",
            "direct-grpc-url": f"{self._fqdn}:{Nginx.parca_grpc_server_port}"
        }

        if http_external_host := self.ingress.http_external_host:
            out["ingressed-http-url"]= f"{http_external_host}:{Nginx.parca_http_server_port}"
        if grpc_external_host := self.ingress.grpc_external_host:
            out["ingressed-grpc-url"]= f"{grpc_external_host}:{Nginx.parca_grpc_server_port}"
        event.set_results(out)

    def _on_get_slo_template_action(self, event: ops.ActionEvent):
        """Handle the get-slo-template action."""
        sli_template_path = Path(__file__).parent / "sli_templates" / "sli.yaml"
        try:
            with open(sli_template_path, "r") as f:
                template = f.read()
            event.set_results({"template": template})
        except FileNotFoundError:
            event.fail(f"SLI template file not found at {sli_template_path}")
        except Exception as e:
            event.fail(f"Failed to read SLI template: {str(e)}")

    def _provide_slos(self):
        """Provide SLO specifications to Sloth via the SLO relation."""
        slo_config = self.config.get("slos", "")
        if not slo_config or not isinstance(slo_config, str):
            return

        try:
            self.slo_provider.provide_slos(slo_config)
            logger.info("Successfully provided SLO specifications to Sloth")
        except Exception as e:
            logger.error(f"Failed to provide SLOs: {e}")



def _generic_scrape_target(
    fqdn: str,
    port: int,
    tls_config_ca_file_key: str,
    scheme="http",
    labels: Optional[Dict[str, str]] = None,
    job_name: Optional[str] = None,
    relabel_configs: Optional[List[RelabelConfig]] = None,
) -> List[ScrapeJobsConfig]:
    """Generate a list of scrape job configs, valid for parca or prometheus."""
    job: ScrapeJob = {"targets": [f"{fqdn}:{port}"]}
    if labels:
        job["labels"] = labels
    jobs_config: ScrapeJobsConfig = {"static_configs": [job]}
    if scheme == "https":
        jobs_config["scheme"] = "https"  # noqa
        if Path(CA_CERT_PATH).exists():
            jobs_config["tls_config"] = {tls_config_ca_file_key: Path(CA_CERT_PATH).read_text()}
    if job_name:
        jobs_config["job_name"] = job_name
    if relabel_configs:
        jobs_config["relabel_configs"] = relabel_configs
    return [jobs_config]


if __name__ == "__main__":  # pragma: nocover
    ops.main(ParcaOperatorCharm)
