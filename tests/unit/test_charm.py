# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

import json
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import ops.testing as testing
import pytest
import yaml
from charms.parca_k8s.v0.parca_config import DEFAULT_CONFIG_PATH
from ops.model import ActiveStatus, WaitingStatus

from charm import ParcaOperatorCharm
from nginx import NGINX_PORT
from parca import PARCA_PORT

DEFAULT_PLAN = {
    "services": {
        "parca": {
            "summary": "parca",
            "startup": "enabled",
            "override": "replace",
            "command": f"/parca --config-path={DEFAULT_CONFIG_PATH} --http-address=localhost:{PARCA_PORT} --storage-active-memory=4294967296",
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


@pytest.fixture
def context():
    return testing.Context(ParcaOperatorCharm)


@pytest.fixture
def parca_peers():
    return testing.PeerRelation("parca-peers")


@pytest.fixture
def parca_container():
    return testing.Container("parca", can_connect=True)


@pytest.fixture
def nginx_container():
    return testing.Container("nginx", can_connect=True)


@pytest.fixture
def prom_exporter_container():
    return testing.Container("nginx-prometheus-exporter", can_connect=True)


@pytest.fixture(autouse=True)
def patch_all():
    with ExitStack() as stack:
        stack.enter_context(patch("parca.Parca.version", "v0.12.0"))
        yield


@pytest.fixture
def base_state(parca_container, nginx_container, prom_exporter_container, parca_peers):
    return testing.State(
        containers={parca_container, nginx_container, prom_exporter_container},
        relations={parca_peers},
    )


def test_pebble_ready(context, parca_container, base_state):
    state_out = context.run(context.on.pebble_ready(parca_container), base_state)

    container_out = state_out.get_container("parca")

    assert container_out.plan.to_dict() == DEFAULT_PLAN
    assert container_out.services["parca"].is_running()
    assert state_out.unit_status == ActiveStatus()
    assert state_out.workload_version == "v0.12.0"


def test_update_status(context, parca_container, base_state):
    state_out = context.run(context.on.update_status(), base_state)
    assert state_out.workload_version == "v0.12.0"


def test_config_changed_container_not_ready(
    context, parca_container, nginx_container, prom_exporter_container, parca_peers
):
    state = testing.State(
        containers={
            replace(parca_container, can_connect=False),
            nginx_container,
            prom_exporter_container,
        },
        relations={parca_peers},
        config={"enable-persistence": False, "memory-storage-limit": 1024},
    )
    state_out = context.run(context.on.config_changed(), state)

    assert state_out.unit_status == WaitingStatus("waiting for container")


def test_config_changed_persistence(context, base_state):
    state_out = context.run(
        context.on.config_changed(),
        replace(base_state, config={"enable-persistence": True, "memory-storage-limit": 1024}),
    )

    container_out = state_out.get_container("parca")

    expected_plan = {
        "services": {
            "parca": {
                "summary": "parca",
                "startup": "enabled",
                "override": "replace",
                "command": f"/parca --config-path={DEFAULT_CONFIG_PATH} --http-address=localhost:{PARCA_PORT} --enable-persistence --storage-path=/var/lib/parca",
            }
        }
    }
    assert container_out.plan.to_dict() == expected_plan
    assert state_out.unit_status == ActiveStatus()


def test_config_changed_active_memory(context, base_state):
    state_out = context.run(
        context.on.config_changed(),
        replace(base_state, config={"enable-persistence": False, "memory-storage-limit": 2048}),
    )

    container_out = state_out.get_container("parca")
    expected_plan = {
        "services": {
            "parca": {
                "summary": "parca",
                "startup": "enabled",
                "override": "replace",
                "command": f"/parca --config-path={DEFAULT_CONFIG_PATH} --http-address=localhost:{PARCA_PORT} --storage-active-memory=2147483648",
            }
        }
    }
    assert container_out.plan.to_dict() == expected_plan
    assert state_out.unit_status == ActiveStatus()


def test_config_file_written(context, parca_container, base_state):
    state_out = context.run(context.on.pebble_ready(parca_container), base_state)
    container_out = state_out.get_container("parca")

    config = container_out.get_filesystem(context).joinpath(
        Path(DEFAULT_CONFIG_PATH).relative_to("/")
    )
    expected = {
        "object_storage": {
            "bucket": {"config": {"directory": "/var/lib/parca"}, "type": "FILESYSTEM"}
        },
        "scrape_configs": [],
    }
    assert yaml.safe_load(config.read_text()) == expected


def test_parca_pebble_layer_adjusted_memory(context, base_state):
    state_out = context.run(
        context.on.config_changed(),
        replace(base_state, config={"enable-persistence": False, "memory-storage-limit": 2048}),
    )
    state_out_2 = context.run(
        context.on.config_changed(),
        replace(state_out, config={"enable-persistence": False, "memory-storage-limit": 1024}),
    )
    expected_plan = {
        "services": {
            "parca": {
                "summary": "parca",
                "startup": "enabled",
                "override": "replace",
                "command": f"/parca --config-path={DEFAULT_CONFIG_PATH} --http-address=localhost:{PARCA_PORT} --storage-active-memory=1073741824",
            }
        }
    }

    container_out = state_out_2.get_container("parca")
    assert container_out.plan.to_dict() == expected_plan
    assert state_out.unit_status == ActiveStatus()


def test_parca_pebble_layer_storage_persist(context, base_state):
    state_out = context.run(
        context.on.config_changed(),
        replace(base_state, config={"enable-persistence": False, "memory-storage-limit": 1024}),
    )
    state_out_2 = context.run(
        context.on.config_changed(),
        replace(state_out, config={"enable-persistence": True, "memory-storage-limit": 1024}),
    )

    expected_plan = {
        "services": {
            "parca": {
                "summary": "parca",
                "startup": "enabled",
                "override": "replace",
                "command": f"/parca --config-path={DEFAULT_CONFIG_PATH} --http-address=localhost:{PARCA_PORT} --enable-persistence --storage-path=/var/lib/parca",
            }
        }
    }
    container_out = state_out_2.get_container("parca")
    assert container_out.plan.to_dict() == expected_plan
    assert state_out.unit_status == ActiveStatus()


def test_profiling_endpoint_relation(context, base_state):
    relation = testing.Relation(
        "profiling-endpoint",
        remote_app_name="profiled-app",
        remote_app_data={
            "scrape_metadata": json.dumps(SCRAPE_METADATA),
            "scrape_jobs": json.dumps(SCRAPE_JOBS),
        },
        remote_units_data={
            0: {
                "parca_scrape_unit_address": "1.1.1.1",
                "parca_scrape_unit_name": "profiled-app/0",
            }
        },
    )
    # Create a relation to an app named "profiled-app"
    # Taking into account the data provided by the simulated app, we should receive the
    # following jobs config from the profiling_consumer
    expected_jobs = [
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
    with context(
        context.on.relation_changed(relation), replace(base_state, relations={relation})
    ) as mgr:
        assert mgr.charm.profiling_consumer.jobs() == expected_jobs
        state_out = mgr.run()

    container_out = state_out.get_container("parca")
    config = container_out.get_filesystem(context).joinpath(
        Path(DEFAULT_CONFIG_PATH).relative_to("/")
    )

    expected_config = {
        "object_storage": {
            "bucket": {"config": {"directory": "/var/lib/parca"}, "type": "FILESYSTEM"}
        },
        "scrape_configs": expected_jobs,
    }
    assert yaml.safe_load(config.read_text()) == expected_config


def test_metrics_endpoint_relation(context, base_state):
    # Create a relation to an app named "prometheus"
    relation = testing.Relation("metrics-endpoint", remote_app_name="prometheus")

    state_out = context.run(
        context.on.relation_joined(relation),
        replace(base_state, leader=True, relations={relation}),
    )

    # Grab the unit data from the relation
    rel_out = state_out.get_relation(relation.id)
    # Ensure that the unit set its targets correctly
    expected = {
        "prometheus_scrape_unit_address": "192.0.2.0",
        "prometheus_scrape_unit_name": "parca-k8s/0",
    }
    for key, val in expected.items():
        assert rel_out.local_unit_data[key] == val


def test_parca_store_relation(context, base_state):
    # Create a relation to an app named "parca-store-endpoint"
    relation = testing.Relation("parca-store-endpoint", remote_app_name="foo")

    state_out = context.run(
        context.on.relation_joined(relation),
        replace(base_state, leader=True, relations={relation}),
    )

    # Grab the unit data from the relation
    rel_out = state_out.get_relation(relation.id)
    # Ensure that the unit set its targets correctly
    expected = {
        "remote-store-address": f"192.0.2.0:{NGINX_PORT}",
        "remote-store-insecure": "true",
    }
    for key, val in expected.items():
        assert rel_out.local_app_data[key] == val


def test_parca_external_store_relation(context, base_state):
    pscloud_config = {
        "remote-store-address": "grpc.polarsignals.com:443",
        "remote-store-bearer-token": "deadbeef",
        "remote-store-insecure": "false",
    }

    relation = testing.Relation(
        "external-parca-store-endpoint", remote_app_name="pscloud", remote_app_data=pscloud_config
    )

    # Ensure that the pscloud config gets passed to the charm
    with context(
        context.on.relation_changed(relation),
        replace(base_state, leader=True, relations={relation}),
    ) as mgr:
        config = mgr.charm.store_requirer.config
        for key, val in pscloud_config.items():
            assert config[key] == val
        state_out = mgr.run()

    # Check the Parca is started with the correct command including store flags
    expected_command = f"/parca --config-path={DEFAULT_CONFIG_PATH} --http-address=localhost:{PARCA_PORT} --storage-active-memory=4294967296 --store-address=grpc.polarsignals.com:443 --bearer-token=deadbeef --insecure=false --mode=scraper-only"
    container_out = state_out.get_container("parca")
    assert container_out.plan.services["parca"].command == expected_command
