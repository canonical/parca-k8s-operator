# Copyright 2025 Canonical
# See LICENSE file for licensing details.

from unittest.mock import MagicMock

import pytest
from six import StringIO

from parca import DEFAULT_CONFIG_PATH, Parca

# Extract from a real response that Parca issued to test the regular expression works for capturing
# the version from the served page.
MOCK_WEB_RESPONSE_V1 = b'<script>window.PATH_PREFIX="",window.APP_VERSION="v0.18.0-2b08f0bd"</s'
MOCK_WEB_RESPONSE_V2 = b'<script>window.PATH_PREFIX="",window.APP_VERSION="v0.18.0"</s>'


@pytest.fixture
def parca():
    container_mock = MagicMock()
    return Parca(
        container=container_mock,
        scrape_configs=[],
        enable_persistence=False,
        memory_storage_limit=1024,
    )


def test_default_pebble_layer(parca):
    expected = {
        "services": {
            "parca": {
                "summary": "parca",
                "startup": "enabled",
                "override": "replace",
                "command": f"/parca "
                           f"--config-path={DEFAULT_CONFIG_PATH} "
                           f"--http-address=localhost:{Parca.port} "
                           "--storage-enable-wal "
                           "--storage-active-memory=1073741824",
            }
        }
    }
    assert parca._pebble_layer() == expected


def _mock_container_exec_return_value(parca, value):
    pebble_exec_out = MagicMock()
    pebble_exec_out.stdout = StringIO(value)
    parca._container.exec.return_value = pebble_exec_out


@pytest.mark.parametrize("version", ("0.18.0-2b08f0bd", "0.18.0"))
def test_fetch_version_valid(parca, version):
    _mock_container_exec_return_value(parca, f"parca, version {version} (commit: b144...db)")
    assert parca.version == version


@pytest.mark.parametrize("version", ("", "booboontu", "42"))
def test_fetch_version_invalid(parca, version):
    _mock_container_exec_return_value(parca, f"parca, version {version} (commit: b144...db)")
    assert parca.version == ""
