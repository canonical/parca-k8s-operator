# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Control Parca running in a container under Pebble. Provides a Parca class."""

import logging
import re
import typing
from typing import Dict, List, Literal, Optional, Sequence, TypedDict, Union

import ops.pebble
import yaml
from ops import Container
from ops.pebble import Layer

from nginx import CA_CERT_PATH

if typing.TYPE_CHECKING:  # pragma: nocover
    from models import S3Config, TLSConfig

logger = logging.getLogger(__name__)

VERSION_PATTERN = re.compile('([0-9]+[.][0-9]+[.][0-9]+[-0-9a-f]*)')
# parca server bind port
_PARCA_PORT = 7070
DEFAULT_BIN_PATH = "/parca"
DEFAULT_CONFIG_PATH = "/etc/parca/parca.yaml"
DEFAULT_PROFILE_PATH = "/var/lib/parca"
S3_TLS_CA_CERT_PATH = "/etc/parca/s3_ca.crt"

ScrapeJob = Dict[str, Union[List[str], Dict[str, str]]]
RelabelConfig = Dict[str, Union[list[str], str]]


class ScrapeJobsConfig(TypedDict, total=False):
    """Scrape job config type."""

    static_configs: List[ScrapeJob]
    profiling_config: Dict[str, str]
    scheme: Optional[Literal["https"]]
    tls_config: Dict[str, str]
    job_name: Optional[str]
    relabel_configs: Optional[List[RelabelConfig]]


class Parca:
    """Parca workload."""

    # Seconds to wait in between requests to version endpoint
    _version_retry_wait = 3

    port = _PARCA_PORT
    service_name = "parca"
    container_name = "parca"
    layer_name = "parca"

    def __init__(
        self,
        container: Container,
        scrape_configs: List[ScrapeJobsConfig],
        enable_persistence: Optional[bool] = None,
        memory_storage_limit: Optional[int] = None,
        store_config: Optional[Dict[str, str]] = None,
        tls_config: Optional["TLSConfig"] = None,
        s3_config: Optional["S3Config"] = None,
        tracing_endpoint: Optional[str] = None,
    ):
        self._container = container
        self._scrape_configs = scrape_configs
        self._enable_persistence = enable_persistence
        self._memory_storage_limit = memory_storage_limit
        self._store_config = store_config
        self._tls_config = tls_config
        self._s3_config = s3_config
        self._tracing_endpoint = tracing_endpoint

    @property
    def _config(self) -> str:
        """YAML-encoded parca config file."""
        return ParcaConfig(self._scrape_configs, s3_config=self._s3_config).to_yaml()

    def reconcile(self):
        """Unconditional control logic."""
        if self._container.can_connect():
            # keep the reconcile_tls_config call on top: otherwise, parca may be configured
            # with tls (and error out on start) before the certs are actually written to disk.
            self._reconcile_tls_config()
            self._reconcile_parca_config()

    def _reconcile_tls_config(self):
        for cert, cert_path in (
            (self._tls_config.certificate.ca.raw if self._tls_config else None, CA_CERT_PATH),
            (self._s3_config.ca_cert if self._s3_config else None, S3_TLS_CA_CERT_PATH),
        ):
            if cert:
                current = (
                    self._container.pull(cert_path).read()
                    if self._container.exists(cert_path)
                    else ""
                )
                if current == cert:
                    continue
                self._container.push(cert_path, cert, make_dirs=True)

            else:
                self._container.remove_path(cert_path, recursive=True)

        # TODO: uncomment when parca container has update-ca-certificates command
        #  and only run if there's been changes.
        # self._container.exec(["update-ca-certificates", "--fresh"])

    def _reconcile_parca_config(self):
        # TODO: https://github.com/canonical/parca-k8s-operator/issues/398
        #  parca hot-reloads config, so we don't need to track changes and restart manually.
        #  it could be useful though, perhaps, to track changes so we can surface to the user
        #  that something has changed.
        self._container.push(
            DEFAULT_CONFIG_PATH, str(self._config), make_dirs=True, permissions=0o644
        )
        layer = self._pebble_layer()
        self._container.add_layer(self.layer_name, layer, combine=True)
        self._container.replan()

    def _pebble_layer(self) -> Layer:
        """Return a Pebble layer for Parca based on the current configuration."""
        env = {}
        if self._tls_config and self._tracing_endpoint:
            env["OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE"] = CA_CERT_PATH
        return Layer(
            {
                "services": {
                    self.service_name: {
                        "override": "replace",
                        "summary": "parca",
                        "command": parca_command_line(
                            # <localhost> prefix is to ensure users can't reach the server at :7070
                            # and are forced to go through nginx instead.
                            http_address=f"localhost:{_PARCA_PORT}",
                            memory_storage_limit=self._memory_storage_limit,
                            enable_persistence=bool(self._enable_persistence or self._s3_config),
                            store_config=self._store_config,
                            tracing_endpoint=self._tracing_endpoint,
                            tracing_tls=bool(self._tls_config),
                        ),
                        "startup": "enabled",
                        "environment": env,
                    }
                },
            }
        )

    @property
    def version(self) -> str:
        """Fetch the version from the binary."""
        try:
            version_out = self._container.exec(["/parca", "--version"]).stdout
        except ops.pebble.Error:
            logger.exception("error attempting to fetch parca version from container")
            return ""

        if not version_out:
            logger.error("unable to get version from parca: `/parca --version` has no stdout.")
            return ""

        match = VERSION_PATTERN.search(version_out.read())
        if not match:
            logger.error(f"unable to get version from parca: `/parca --version` returned {version_out!r}, "
                         f"which didn't match the expected {VERSION_PATTERN.pattern!r}")
            return ""
        return match.groups()[0]


def parca_command_line(
    http_address: str = f":{_PARCA_PORT}",
    enable_persistence: Optional[bool] = False,
    memory_storage_limit: Optional[int] = None,
    *,
    bin_path: str = DEFAULT_BIN_PATH,
    config_path: str = DEFAULT_CONFIG_PATH,
    profile_path: str = DEFAULT_PROFILE_PATH,
    store_config: Optional[dict] = None,
    tracing_endpoint: Optional[str] = None,
    tracing_tls: bool = False,
) -> str:
    """Generate a valid Parca command line.

    Args:
        http_address: Http address for the parca server.
        enable_persistence: Whether to enable the filesystem persistence feature.
        memory_storage_limit: Memory storage limit.
        bin_path: Path to the Parca binary to be started.
        config_path: Path to the Parca YAML configuration file.
        profile_path: Path to profile storage directory.
        store_config: Configuration to send profiles to a remote store
        tracing_endpoint: Address to send traces to.
        tracing_tls: If true, enable sending traces over TLS.
    """
    cmd = [str(bin_path),
           f"--config-path={config_path}",
           f"--http-address={http_address}",
           "--storage-enable-wal"]

    if enable_persistence:
        # Add the correct command line options for disk persistence
        cmd.append("--enable-persistence")
        cmd.append(f"--storage-path={profile_path}")
    else:
        limit = (memory_storage_limit or 1024) * 1048576
        cmd.append(f"--storage-active-memory={limit}")

    if store_config is not None:
        store_config_args = []

        if addr := store_config.get("remote-store-address", None):
            store_config_args.append(f"--store-address={addr}")

        if token := store_config.get("remote-store-bearer-token", None):
            store_config_args.append(f"--bearer-token={token}")

        if insecure := store_config.get("remote-store-insecure", None):
            store_config_args.append(f"--insecure={insecure}")

        if store_config_args:
            store_config_args.append("--mode=scraper-only")
            cmd += store_config_args

    if tracing_endpoint:
        cmd.append(f"--otlp-address={tracing_endpoint}")
        if tracing_tls:
            cmd.append("--otlp-insecure=false")

    return " ".join(cmd)


class ParcaConfig:
    """Class representing the Parca config file."""

    def __init__(
        self,
        scrape_configs: Optional[Sequence[ScrapeJobsConfig]] = None,
        s3_config: Optional["S3Config"] = None,
        *,
        profile_path=DEFAULT_PROFILE_PATH,
    ):
        self._profile_path = str(profile_path)
        self._scrape_configs = scrape_configs or []
        self._s3_config = s3_config

    def _parca_s3_config(self, s3_config: "S3Config"):
        # Strip protocol scheme (http:// or https://) from endpoint as Parca expects just hostname:port
        endpoint = s3_config.endpoint.removeprefix("https://").removeprefix("http://")

        bucket_config = {
            "bucket": s3_config.bucket,
            "region": s3_config.region,
            "endpoint": endpoint,
            "secret_key": s3_config.secret_key,
            "access_key": s3_config.access_key,
            "insecure": not s3_config.ca_cert,
        }
        if s3_config.ca_cert:
            http_config = {
                "tls_config": {
                    "ca_file": S3_TLS_CA_CERT_PATH,
                    "insecure_skip_verify": False,
                }
            }
            bucket_config["http_config"] = http_config

        return {
            "type": "S3",
            "config": bucket_config,
        }

    @property
    def _config(self) -> dict:
        if s3_config := self._s3_config:
            bucket_spec = self._parca_s3_config(s3_config=s3_config)
        else:
            bucket_spec = {"type": "FILESYSTEM", "config": {"directory": self._profile_path}}

        return {
            "object_storage": {"bucket": bucket_spec},
            "scrape_configs": self._scrape_configs,
        }

    def to_dict(self) -> dict:
        """Return the Parca config as a Python dictionary."""
        return self._config

    def to_yaml(self) -> str:
        """Return the Parca config as a YAML string."""
        return yaml.safe_dump(self._config)
