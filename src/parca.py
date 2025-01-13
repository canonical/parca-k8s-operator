# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Control Parca running in a container under Pebble. Provides a Parca class."""

import logging
import re
import time
import urllib.request
from typing import Optional

from charms.parca_k8s.v0.parca_config import ParcaConfig, parca_command_line
from ops.pebble import Layer

logger = logging.getLogger(__name__)

# This pattern is for parsing the Parca version from the HTML page returned by Parca.
# A bit hacky, but the API is more complex to use (gRPC) and the version string
# reported by the Prometheus metrics is wrong at the time of writing.
VERSION_PATTERN = re.compile('APP_VERSION="v([0-9]+[.][0-9]+[.][0-9]+[-0-9a-f]*)"')


class Parca:
    """Class representing Parca running in a container under Pebble."""

    _port = 7070
    # Seconds to wait in between requests to version endpoint
    _version_retry_wait = 3

    def pebble_layer(self, config, store_config=None, path_prefix:Optional[str]=None) -> Layer:
        """Return a Pebble layer for Parca based on the current configuration."""
        return Layer(
            {
                "services": {
                    "parca": {
                        "override": "replace",
                        "summary": "parca",
                        "command": parca_command_line(
                            app_config=config,
                            store_config=store_config or {},
                            path_prefix=path_prefix
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
    def port(self) -> int:
        """Report the TCP port that Parca is configured to listen on."""
        return self._port

    @property
    def version(self) -> str:
        """Report the version of Parca."""
        return self._fetch_version()

    def _fetch_version(self) -> str:
        """Fetch the version from the running workload using the Parca API."""
        retries = 0
        while True:
            try:
                res = urllib.request.urlopen(f"http://localhost:{self._port}")
                m = VERSION_PATTERN.search(res.read().decode())
                if m is None:
                    return ""
                return m.groups()[0]
            except Exception:
                if retries == 2:
                    return ""
                retries += 1
                time.sleep(self._version_retry_wait)
