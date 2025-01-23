# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Control Parca running in a container under Pebble. Provides a Parca class."""

import logging
import re
import time
import urllib.request
from typing import Optional

from charms.parca_k8s.v0.parca_config import ParcaConfig, parca_command_line
from ops import Container
from ops.pebble import Layer

from nginx import CA_CERT_PATH

logger = logging.getLogger(__name__)

# This pattern is for parsing the Parca version from the HTML page returned by Parca.
# A bit hacky, but the API is more complex to use (gRPC) and the version string
# reported by the Prometheus metrics is wrong at the time of writing.
VERSION_PATTERN = re.compile('APP_VERSION="v([0-9]+[.][0-9]+[.][0-9]+[-0-9a-f]*)"')
# parca server bind port
PARCA_PORT = 7070


class Parca:
    """Class representing Parca running in a container under Pebble."""

    # Seconds to wait in between requests to version endpoint
    _version_retry_wait = 3

    def __init__(
        self,
        container: Container,
    ):
        self._container = container

    def pebble_layer(self, config, store_config=None, path_prefix: Optional[str] = None) -> Layer:
        """Return a Pebble layer for Parca based on the current configuration."""
        return Layer(
            {
                "services": {
                    "parca": {
                        "override": "replace",
                        "summary": "parca",
                        "command": parca_command_line(
                            # <localhost> prefix is to ensure users can't reach the server at :7070
                            # and are forced to go through nginx instead.
                            http_address=f"localhost:{PARCA_PORT}",
                            app_config=config,
                            store_config=store_config or {},
                            path_prefix=path_prefix,
                        ),
                        "startup": "enabled",
                    }
                },
            }
        )

    def generate_config(self, scrape_configs=[]):
        """Generate a Parca configuration."""
        return ParcaConfig(scrape_configs)

    @property
    def version(self) -> str:
        """Report the version of Parca."""
        return self._fetch_version()

    def _fetch_version(self) -> str:
        """Fetch the version from the running workload using the Parca API."""
        retries = 0
        while True:
            try:
                res = urllib.request.urlopen(f"http://localhost:{PARCA_PORT}")
                m = VERSION_PATTERN.search(res.read().decode())
                if m is None:
                    return ""
                return m.groups()[0]
            except Exception:
                if retries == 2:
                    return ""
                retries += 1
                time.sleep(self._version_retry_wait)

    def update_ca_certificate(self, ca_cert: str) -> None:
        """Save the CA certificate file to disk and run update-ca-certificates."""
        if self._container.can_connect():
            current_ca_cert = (
                self._container.pull(CA_CERT_PATH).read()
                if self._container.exists(CA_CERT_PATH)
                else ""
            )
            if current_ca_cert == ca_cert:
                # No update needed
                return

            self._container.push(CA_CERT_PATH, ca_cert, make_dirs=True)

            # TODO: uncomment when parca container has update-ca-certificates command
            # self._container.exec(["update-ca-certificates", "--fresh"])

    def delete_ca_certificate(self):
        """Delete the CA certificate file from disk and run update-ca-certificates."""
        if self._container.can_connect():
            if self._container.exists(CA_CERT_PATH):
                self._container.remove_path(CA_CERT_PATH, recursive=True)
            # TODO: uncomment when parca container has update-ca-certificates command
            # self._container.exec(["update-ca-certificates", "--fresh"])
