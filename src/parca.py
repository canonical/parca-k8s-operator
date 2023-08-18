# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

"""Control Parca running in a container under Pebble. Provides a Parca class."""

import logging
import re
import time
import urllib.request

from charms.parca.v0.parca_config import ParcaConfig, parca_command_line

logger = logging.getLogger(__name__)

# This pattern is for parsing the Parca version from the HTML page returned by Parca.
# A bit hacky, but the API is more complex to use (gRPC) and the version string
# reported by the Prometheus metrics is wrong at the time of writing.
VERSION_PATTERN = re.compile('APP_VERSION="([0-9]+[.][0-9]+[.][0-9]+)"')


class Parca:
    """Class representing Parca running in a container under Pebble."""

    _port = 7070

    def pebble_layer(self, config) -> dict:
        """Return a Pebble layer for Parca based on the current configuration."""
        return {
            "services": {
                "parca": {
                    "override": "replace",
                    "summary": "parca",
                    "command": parca_command_line(config),
                    "startup": "enabled",
                }
            },
        }

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
        try:
            return self._fetch_version()
        except Exception:
            return ""

    def _fetch_version(self) -> str:
        """Fetch the version from the running workload using the Parca API."""
        retries = 0
        while True:
            try:
                res = urllib.request.urlopen(f"http://localhost:{self._port}")
                text = res.read().decode()
                m = VERSION_PATTERN.search(text)
                return m.groups()[0]
            except Exception:
                if retries == 3:
                    raise
                retries += 1
                time.sleep(3)
