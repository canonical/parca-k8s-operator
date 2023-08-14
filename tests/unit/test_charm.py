# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import ops.testing
import yaml
from charm import ParcaOperatorCharm
from ops.model import ActiveStatus, WaitingStatus
from ops.pebble import ExecError
from ops.testing import Harness

ops.testing.SIMULATE_CAN_CONNECT = True

DEFAULT_PLAN = {
    "services": {
        "parca": {
            "summary": "parca",
            "startup": "enabled",
            "override": "replace",
            "command": "/parca --config-path=/etc/parca/parca.yaml --storage-active-memory=4294967296",
        }
    }
}

_uuid = uuid4()

SCRAPE_METADATA = {
    "model": "test-model",
    "model_uuid": str(_uuid),
    "application": "profiled-app",
    "charm_name": "test-charm",
}
SCRAPE_JOBS = [
    {
        "global": {"scrape_interval": "1h"},
        "job_name": "my-first-job",
        "static_configs": [{"targets": ["*:7000"], "labels": {"some-key": "some-value"}}],
    },
]


@patch("charm.ParcaOperatorCharm._fetch_version", lambda x: "p, v v0.12.0 (commit: deadbeef")
class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(ParcaOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_pebble_ready(self):
        self.harness.container_pebble_ready("parca")
        self.assertEqual(self.harness.charm.container.get_plan().to_dict(), DEFAULT_PLAN)
        self.assertTrue(self.harness.charm.container.get_service("parca").is_running())
        self.assertEqual(self.harness.charm.unit.status, ActiveStatus())
        self.assertEqual(self.harness.get_workload_version(), "v0.12.0")

    def test_update_status(self):
        self.harness.set_can_connect("parca", True)
        self.harness.charm.on.update_status.emit()
        self.assertEqual(self.harness.get_workload_version(), "v0.12.0")

    def test_config_changed_container_not_ready(self):
        self.harness.update_config({"enable-persistence": False, "memory-storage-limit": 1024})
        self.assertEqual(self.harness.charm.unit.status, WaitingStatus("waiting for container"))

    def test_config_changed_persistence(self):
        self.harness.container_pebble_ready("parca")
        self.harness.set_can_connect("parca", True)
        self.assertEqual(self.harness.charm.container.get_plan().to_dict(), DEFAULT_PLAN)
        self.harness.update_config({"enable-persistence": True, "memory-storage-limit": 1024})
        expected_plan = {
            "services": {
                "parca": {
                    "summary": "parca",
                    "startup": "enabled",
                    "override": "replace",
                    "command": "/parca --config-path=/etc/parca/parca.yaml --enable-persistence --storage-path=/var/lib/parca",
                }
            }
        }
        self.assertEqual(self.harness.charm.container.get_plan().to_dict(), expected_plan)
        self.assertEqual(self.harness.charm.unit.status, ActiveStatus())

    def test_config_changed_active_memory(self):
        self.harness.container_pebble_ready("parca")
        self.harness.set_can_connect("parca", True)
        self.harness.update_config({"enable-persistence": False, "memory-storage-limit": 2048})
        expected_plan = {
            "services": {
                "parca": {
                    "summary": "parca",
                    "startup": "enabled",
                    "override": "replace",
                    "command": "/parca --config-path=/etc/parca/parca.yaml --storage-active-memory=2147483648",
                }
            }
        }
        self.assertEqual(self.harness.charm.container.get_plan().to_dict(), expected_plan)
        self.assertEqual(self.harness.charm.unit.status, ActiveStatus())

    def test_configure(self):
        self.harness.container_pebble_ready("parca")
        self.harness.charm._configure()

        config = self.harness.charm.container.pull("/etc/parca/parca.yaml")
        expected = {
            "object_storage": {
                "bucket": {"config": {"directory": "/var/lib/parca"}, "type": "FILESYSTEM"}
            },
            "scrape_configs": [],
        }
        self.assertEqual(yaml.safe_load(config.read()), expected)

        self.harness.charm._configure(scrape_configs=[{"foobar": "baz"}])
        config = self.harness.charm.container.pull("/etc/parca/parca.yaml")
        expected = {
            "object_storage": {
                "bucket": {"config": {"directory": "/var/lib/parca"}, "type": "FILESYSTEM"}
            },
            "scrape_configs": [{"foobar": "baz"}],
        }
        self.assertEqual(yaml.safe_load(config.read()), expected)

    def test_parca_pebble_layer_default_config(self):
        self.assertEqual(DEFAULT_PLAN, self.harness.charm._pebble_layer.to_dict())

    def test_parca_pebble_layer_adjusted_memory(self):
        self.harness.update_config({"enable-persistence": False, "memory-storage-limit": 1024})
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
        self.assertEqual(expected, self.harness.charm._pebble_layer.to_dict())

    def test_parca_pebble_layer_storage_persist(self):
        self.harness.update_config({"enable-persistence": True, "memory-storage-limit": 1024})
        expected = {
            "services": {
                "parca": {
                    "summary": "parca",
                    "startup": "enabled",
                    "override": "replace",
                    "command": "/parca --config-path=/etc/parca/parca.yaml --enable-persistence --storage-path=/var/lib/parca",
                }
            }
        }
        self.assertEqual(expected, self.harness.charm._pebble_layer.to_dict())

    def test_version_not_started(self):
        vstr = self.harness.charm.version
        self.assertEqual(vstr, "")

    def test_version(self):
        self.harness.set_can_connect("parca", True)
        vstr = self.harness.charm.version
        self.assertEqual(vstr, "v0.12.0")

    def test_profiling_endpoint_relation(self):
        # Create a relation to an app named "profiled-app"
        rel_id = self.harness.add_relation("profiling-endpoint", "profiled-app")
        # Simulate that "profiled-app" has provided the data we're expecting
        self.harness.update_relation_data(
            rel_id,
            "profiled-app",
            {
                "scrape_metadata": json.dumps(SCRAPE_METADATA),
                "scrape_jobs": json.dumps(SCRAPE_JOBS),
            },
        )
        # Add a unit to the relation
        self.harness.add_relation_unit(rel_id, "profiled-app/0")
        # Simulate the remote unit adding its details for scraping
        self.harness.update_relation_data(
            rel_id,
            "profiled-app/0",
            {
                "parca_scrape_unit_address": "1.1.1.1",
                "parca_scrape_unit_name": "profiled-app/0",
            },
        )
        # Taking into account the data provided by the simulated app, we should receive the
        # following jobs config from the profiling_consumer
        expected = [
            {
                "static_configs": [
                    {
                        "labels": {
                            "some-key": "some-value",
                            "juju_model": "test-model",
                            "juju_model_uuid": str(_uuid),
                            "juju_application": "profiled-app",
                            "juju_charm": "test-charm",
                            "juju_unit": "profiled-app/0",
                        },
                        "targets": ["1.1.1.1:7000"],
                    }
                ],
                "job_name": f"test-model_{str(_uuid).split('-')[0]}_profiled-app_my-first-job",
                "relabel_configs": [
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
                ],
            }
        ]
        self.assertEqual(self.harness.charm.profiling_consumer.jobs(), expected)

    @patch("ops.model.Model.get_binding", lambda *args: MockBinding("10.10.10.10"))
    def test_metrics_endpoint_relation(
        self,
    ):
        # Create a relation to an app named "prometheus"
        rel_id = self.harness.add_relation("metrics-endpoint", "prometheus")
        # Add a prometheus unit
        self.harness.add_relation_unit(rel_id, "prometheus/0")
        # Ugly re-init workaround: manually call `set_scrape_job_spec`
        # https://github.com/canonical/operator/issues/736
        self.harness.charm.metrics_endpoint_provider.set_scrape_job_spec()
        # Grab the unit data from the relation
        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
        # Ensure that the unit set its targets correctly
        expected = {
            "prometheus_scrape_unit_address": "10.10.10.10",
            "prometheus_scrape_unit_name": "parca-k8s/0",
        }
        self.assertEqual(unit_data, expected)

    @patch("ops.model.Model.get_binding", lambda *args: MockBinding("10.10.10.10"))
    def test_parca_store_relation(self):
        self.harness.set_leader(True)
        # Create a relation to an app named "parca-agent"
        rel_id = self.harness.add_relation("parca-store-endpoint", "parca-agent")
        # Add a parca-agent unit
        self.harness.add_relation_unit(rel_id, "parca-agent/0")
        # Grab the unit data from the relation
        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)
        # Ensure that the unit set its targets correctly
        expected = {
            "remote-store-address": "10.10.10.10:7070",
            "remote-store-insecure": "true",
        }
        self.assertEqual(unit_data, expected)


class MockBinding:
    def __init__(self, addr):
        self.network = SimpleNamespace(bind_address=addr)


class MockExec:
    def __init__(self, stdout, stderr=""):
        self.stdout = stdout
        self.stderr = stderr

    def wait_output(self):
        return self.stdout, self.stderr


class TestVersionFetch(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(ParcaOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch("ops.model.Container.exec")
    def test_fetch_version_error(self, exec):
        exec.side_effect = ExecError("foobar", 1, "", "")
        try:
            self.harness.charm._fetch_version()
        except ExecError as e:
            self.assertEqual(
                "non-zero exit code 1 executing 'foobar', stdout='', stderr=''", str(e)
            )

    @patch("ops.model.Container.exec")
    def test_fetch_version_success(self, exec):
        return_str = "p, v v0.12.0 (commit: deadbeef"
        exec.return_value = MockExec(return_str)
        version = self.harness.charm._fetch_version()
        self.assertEqual(version, return_str)
