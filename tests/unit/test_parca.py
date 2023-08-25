# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

import unittest
from unittest.mock import MagicMock, patch

from parca import Parca

# Extract from a real response that Parca issued to test the regular expression works for capturing
# the version from the served page.
MOCK_WEB_RESPONSE_V1 = b'<script>window.PATH_PREFIX="",window.APP_VERSION="v0.18.0-2b08f0bd"</s'
MOCK_WEB_RESPONSE_V2 = b'<script>window.PATH_PREFIX="",window.APP_VERSION="v0.18.0"</s>'


class TestParca(unittest.TestCase):
    def setUp(self):
        self.parca = Parca()

    def test_pebble_layer(self):
        expected = {
            "services": {
                "parca": {
                    "summary": "parca",
                    "startup": "enabled",
                    "override": "replace",
                    "command": "/parca --config-path=/etc/parca/parca.yaml --storage-active-memory=1073741824",
                }
            }
        }
        self.assertEqual(
            self.parca.pebble_layer({"enable-persistence": False, "memory-storage-limit": 1024}),
            expected,
        )

    @patch("urllib.request.urlopen")
    def test_fetch_version_no_commit_hash_suffix(self, uo):
        m = MagicMock()
        m.getcode.return_value = 200
        m.read.return_value = MOCK_WEB_RESPONSE_V1
        uo.return_value = m

        self.assertEqual(self.parca._fetch_version(), "0.18.0-2b08f0bd")

    @patch("urllib.request.urlopen")
    def test_fetch_version_with_commit_hash_suffix(self, uo):
        m = MagicMock()
        m.getcode.return_value = 200
        m.read.return_value = MOCK_WEB_RESPONSE_V2
        uo.return_value = m

        self.assertEqual(self.parca._fetch_version(), "0.18.0")

    @patch("urllib.request.urlopen")
    def test_version_fetch_raises_empty_string_response(self, fv):
        fv.side_effect = Exception
        # Don't wait 3 seconds in between retry calls
        self.parca._version_retry_wait = 0.1
        self.assertEqual(self.parca.version, "")

    @patch("urllib.request.urlopen")
    def test_fetch_version_retries(self, uo):
        uo.side_effect = Exception
        # Don't wait 3 seconds in between retry calls
        self.parca._version_retry_wait = 0.1
        self.parca._fetch_version()
        self.assertEqual(uo.call_count, 3)
