# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

import unittest
from unittest.mock import MagicMock, patch

from parca import Parca

MOCK_WEB_RESPONSE = b'<!doctype html><html lang="en"><head><script>window.PATH_PREFIX="",window.APP_VERSION="0.18.0"</script><meta charset="utf-8"/><link rel="icon" href="/favicon.svg"/><meta name="viewport" content="width=device-width,initial-scale=1"/><meta name="theme-color" content="#000000"/><meta name="description" content="Open Source Infrastructure-wide continuous profiling"/><link rel="apple-touch-icon" href="/logo192.png"/><link rel="manifest" href="/manifest.json"/><title>Parca</title><script defer="defer" src="/static/js/main.6f6e3e59.js"></script><link href="/static/css/main.c536e625.css" rel="stylesheet"></head><body class="bg-gray-50 text-gray-800 dark:bg-gray-900 dark:text-gray-200"><noscript>You need to enable JavaScript to run this app.</noscript><div id="root"></div></body></html>'


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
    def test_fetch_version(self, uo):
        m = MagicMock()
        m.getcode.return_value = 200
        m.read.return_value = MOCK_WEB_RESPONSE
        uo.return_value = m

        self.assertEqual(self.parca._fetch_version(), "0.18.0")

    @patch("parca.Parca._fetch_version")
    def test_version_fetch_raises_empty_string_response(self, fv):
        fv.side_effect = Exception
        self.assertEqual(self.parca.version, "")
