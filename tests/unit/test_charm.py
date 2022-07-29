# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

# This file contains basic tests simply to ensure that the various event handlers for operator
# framework are being called, and that they in turn are invoking the right helpers.
#
# The helpers themselves require too much mocking, and are validated in functional/integration
# tests.


import json
import unittest
from io import StringIO
from subprocess import CalledProcessError
from unittest.mock import PropertyMock, patch

import ops.testing
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.pebble import ExecError
from ops.testing import Harness

from charm import ParcaOperatorCharm

ops.testing.SIMULATE_CAN_CONNECT = True

SCRAPE_METADATA = {
    "model": "test-model",
    "model_uuid": "abcdef",
    "application": "profiled-app",
    "charm_name": "test-charm",
}
SCRAPE_JOBS = [
    {
        "global": {"scrape_interval": "1h"},
        "rule_files": ["/some/file"],
        "file_sd_configs": [{"files": "*some-files*"}],
        "job_name": "my-first-job",
        "metrics_path": "/one-path",
        "static_configs": [{"targets": ["*:7000"], "labels": {"some-key": "some-value"}}],
    },
]


class MockExec:
    def __init__(self, stdout, stderr=""):
        self.stdout = stdout
        self.stderr = stderr

    def wait_output(self):
        return StringIO(self.stdout), StringIO(self.stderr)


class TestCharm(unittest.TestCase):
    @patch("charm.KubernetesServicePatch", lambda x, y: True)
    def setUp(self):
        self.harness = Harness(ParcaOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch("ops.model.Container.exec")
    def test_parca_version_next(self, exec):
        self.harness.set_can_connect("parca", True)
        exec.return_value = MockExec("parca, version v0.12.0-next (commit: deadbeef)\n")
        self.assertEqual(self.harness.charm.version, "v0.12.0-next+deadbe")

    @patch("ops.model.Container.exec")
    def test_parca_version_tagged(self, exec):
        self.harness.set_can_connect("parca", True)
        exec.return_value = MockExec("parca, version v0.13.0 (commit: deadbeef")
        self.assertEqual(self.harness.charm.version, "v0.13.0")

    @patch("ops.model.Container.exec")
    def test_parca_version_tagged(self, exec):
        exec.side_effect = ExecError("foobar", 1, "", "")
        try:
            self.harness.charm.version
        except ExecError as e:
            self.assertEqual(
                "non-zero exit code 1 executing 'foobar', stdout='', stderr=''", str(e)
            )

    def test_parca_pebble_layer_default_config(self):
        expected = {
            "services": {
                "parca": {
                    "summary": "parca",
                    "startup": "enabled",
                    "override": "replace",
                    "command": "/parca --config-path=/etc/parca/parca.yaml --storage-in-memory=true --storage-active-memory=4294967296",
                }
            }
        }
        self.assertEqual(expected, self.harness.charm._pebble_layer.to_dict())

    def test_parca_pebble_layer_adjusted_memory(self):
        self.harness.update_config({"storage-persist": False, "memory-storage-limit": 1024})
        expected = {
            "services": {
                "parca": {
                    "summary": "parca",
                    "startup": "enabled",
                    "override": "replace",
                    "command": "/parca --config-path=/etc/parca/parca.yaml --storage-in-memory=true --storage-active-memory=1073741824",
                }
            }
        }
        self.assertEqual(expected, self.harness.charm._pebble_layer.to_dict())

    def test_parca_pebble_layer_storage_persist(self):
        self.harness.update_config({"storage-persist": True, "memory-storage-limit": 1024})
        expected = {
            "services": {
                "parca": {
                    "summary": "parca",
                    "startup": "enabled",
                    "override": "replace",
                    "command": "/parca --config-path=/etc/parca/parca.yaml --storage-in-memory=false --storage-persist --storage-path=/var/lib/parca",
                }
            }
        }
        self.assertEqual(expected, self.harness.charm._pebble_layer.to_dict())

    # @patch("charm.snap.hold_refresh")
    # @patch("parca.Parca.version", new_callable=PropertyMock(return_value="v0.12.0"))
    # def test_update_status(self, _, hold):
    #     self.harness.charm.on.update_status.emit()
    #     hold.assert_called_once()
    #     self.assertEqual(self.harness.get_workload_version(), "v0.12.0")

    # @patch("charm.Parca.configure")
    # def test_config_changed(self, configure):
    #     config = {
    #         "storage-persist": False,
    #         "memory-storage-limit": 1024,
    #     }
    #     self.harness.update_config(config)
    #     configure.assert_called_with(config, [])
    #     self.assertEqual(self.harness.charm.unit.status, ActiveStatus())

    # @patch("charm.Parca.configure")
    # def test_profiling_endpoint_relation(self, _):
    #     # Create a relation to an app named "profiled-app"
    #     rel_id = self.harness.add_relation("profiling-endpoint", "profiled-app")
    #     # Simulate that "profiled-app" has provided the data we're expecting
    #     self.harness.update_relation_data(
    #         rel_id,
    #         "profiled-app",
    #         {
    #             "scrape_metadata": json.dumps(SCRAPE_METADATA),
    #             "scrape_jobs": json.dumps(SCRAPE_JOBS),
    #         },
    #     )
    #     # Add a unit to the relation
    #     self.harness.add_relation_unit(rel_id, "profiled-app/0")
    #     # Simulate the remote unit adding its details for scraping
    #     self.harness.update_relation_data(
    #         rel_id,
    #         "profiled-app/0",
    #         {
    #             "prometheus_scrape_unit_address": "1.1.1.1",
    #             "prometheus_scrape_unit_name": "profiled-app/0",
    #         },
    #     )
    #     # Taking into account the data provided by the simulated app, we should receive the
    #     # following jobs config from the profiling_consumer
    #     expected = [
    #         {
    #             "metrics_path": "/one-path",
    #             "static_configs": [
    #                 {
    #                     "labels": {
    #                         "some-key": "some-value",
    #                         "juju_model": "test-model",
    #                         "juju_model_uuid": "abcdef",
    #                         "juju_application": "profiled-app",
    #                         "juju_charm": "test-charm",
    #                         "juju_unit": "profiled-app/0",
    #                     },
    #                     "targets": ["1.1.1.1:7000"],
    #                 }
    #             ],
    #             "job_name": "juju_test-model_abcdef_profiled-app_test-charm_prometheus_scrape_my-first-job",
    #             "relabel_configs": [
    #                 {
    #                     "source_labels": [
    #                         "juju_model",
    #                         "juju_model_uuid",
    #                         "juju_application",
    #                         "juju_unit",
    #                     ],
    #                     "separator": "_",
    #                     "target_label": "instance",
    #                     "regex": "(.*)",
    #                 }
    #             ],
    #         }
    #     ]
    #     self.assertEqual(self.harness.charm.profiling_consumer.jobs(), expected)

    # @patch("charm.Parca.configure")
    # @patch("socket.getfqdn", new=lambda *args: "some.host")
    # def test_metrics_endpoint_relation(self, _):
    #     # Create a relation to an app named "prometheus"
    #     rel_id = self.harness.add_relation("metrics-endpoint", "prometheus")
    #     # Add a prometheus unit
    #     self.harness.add_relation_unit(rel_id, "prometheus/0")
    #     # Grab the unit data from the relation
    #     unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
    #     # Ensure that the unit set its targets correctly
    #     expected = {
    #         "prometheus_scrape_unit_address": "some.host",
    #         "prometheus_scrape_unit_name": "parca/0",
    #     }
    #     self.assertEqual(unit_data, expected)
