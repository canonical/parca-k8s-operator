from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True, scope="session")
def diable_charm_tracing_buffer():
    with patch("charms.tempo_coordinator_k8s.v0.charm_tracing._BufferedExporter.export", lambda _, __: None):
        yield
