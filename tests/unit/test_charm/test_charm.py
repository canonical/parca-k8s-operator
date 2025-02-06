# Copyright 2025 Canonical
# See LICENSE file for licensing details.

import json
import socket
from dataclasses import replace
from uuid import uuid4

import pytest
from ops.model import ActiveStatus, WaitingStatus
from ops.testing import CharmEvents, Relation, State

from charm import RELABEL_CONFIG
from nginx import NGINX_PORT
from parca import DEFAULT_CONFIG_PATH, PARCA_PORT
from tests.unit.test_charm.container_utils import (
    assert_parca_command_equals,
    assert_parca_config_equals,
)

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
def base_state(parca_container, nginx_container, nginx_prometheus_exporter_container, parca_peers):
    return State(
        containers={parca_container, nginx_container, nginx_prometheus_exporter_container},
        relations={parca_peers},
    )


def assert_healthy(state: State):
    # check the parca container has a plan and "parca" service is running
    container_out = state.get_container("parca")
    assert container_out.services["parca"].is_running()

    # check the unit status is active
    assert isinstance(state.unit_status, ActiveStatus)

    # check the workload version is set and as expected
    assert state.workload_version == "v0.12.0"


@pytest.fixture(params=(0, 1, 2))
def any_container(parca_container, nginx_container, nginx_prometheus_exporter_container, request):
    # parametrized fixture to allow running tests on any individual container
    return (parca_container, nginx_container, nginx_prometheus_exporter_container)[request.param]


def test_healthy_container_events(context, any_container, base_state):
    state_out = context.run(context.on.pebble_ready(any_container), base_state)
    assert_healthy(state_out)


@pytest.mark.parametrize(
    "event",
    (
        CharmEvents().update_status(),
        CharmEvents().start(),
        CharmEvents().install(),
        CharmEvents().config_changed(),
    ),
)
def test_healthy_lifecycle_events(context, event, base_state):
    state_out = context.run(event, base_state)
    assert_healthy(state_out)


def test_config_changed_container_not_ready(
    context, parca_container, nginx_container, nginx_prometheus_exporter_container, parca_peers
):
    state = State(
        containers={
            replace(parca_container, can_connect=False),
            nginx_container,
            nginx_prometheus_exporter_container,
        },
        relations={parca_peers},
        config={"enable-persistence": False, "memory-storage-limit": 1024},
    )
    state_out = context.run(context.on.config_changed(), state)
    assert state_out.unit_status == WaitingStatus(f"Waiting for containers: {['parca']}...")


def test_config_changed_persistence(context, base_state):
    state_out = context.run(
        context.on.config_changed(),
        replace(base_state, config={"enable-persistence": True, "memory-storage-limit": 1024}),
    )
    assert_parca_command_equals(
        state_out,
        f"/parca --config-path={DEFAULT_CONFIG_PATH} "
        f"--http-address=localhost:{PARCA_PORT} "
        "--storage-enable-wal "
        "--enable-persistence "
        "--storage-path=/var/lib/parca",
    )
    assert_healthy(state_out)


def test_config_changed_active_memory(context, base_state):
    state_out = context.run(
        context.on.config_changed(),
        replace(base_state, config={"enable-persistence": False, "memory-storage-limit": 2048}),
    )

    assert_parca_command_equals(
        state_out,
        f"/parca "
        f"--config-path={DEFAULT_CONFIG_PATH} "
        f"--http-address=localhost:{PARCA_PORT} "
        "--storage-enable-wal "
        f"--storage-active-memory=2147483648",
    )
    assert_healthy(state_out)


def test_config_file_written(context, parca_container, base_state):
    self_profiling = Relation(
        "self-profiling-endpoint",
    )

    state_out = context.run(
        context.on.pebble_ready(parca_container), replace(base_state, relations={self_profiling})
    )
    assert_parca_config_equals(
        context,
        state_out,
        {
            "object_storage": {
                "bucket": {"config": {"directory": "/var/lib/parca"}, "type": "FILESYSTEM"}
            },
            "scrape_configs": [],
        },
    )


def test_parca_pebble_layer_adjusted_memory(context, base_state):
    state_out = context.run(
        context.on.config_changed(),
        replace(base_state, config={"enable-persistence": False, "memory-storage-limit": 2048}),
    )
    state_out_2 = context.run(
        context.on.config_changed(),
        replace(state_out, config={"enable-persistence": False, "memory-storage-limit": 1024}),
    )
    assert_parca_command_equals(
        state_out_2,
        f"/parca "
        f"--config-path={DEFAULT_CONFIG_PATH} "
        f"--http-address=localhost:{PARCA_PORT} "
        "--storage-enable-wal "
        f"--storage-active-memory=1073741824",
    )
    assert_healthy(state_out_2)


def test_parca_pebble_layer_storage_persist(context, base_state):
    state_out = context.run(
        context.on.config_changed(),
        replace(base_state, config={"enable-persistence": False, "memory-storage-limit": 1024}),
    )
    state_out_2 = context.run(
        context.on.config_changed(),
        replace(state_out, config={"enable-persistence": True, "memory-storage-limit": 1024}),
    )

    assert_parca_command_equals(
        state_out_2,
        f"/parca "
        f"--config-path={DEFAULT_CONFIG_PATH} "
        f"--http-address=localhost:{PARCA_PORT} "
        "--storage-enable-wal "
        "--enable-persistence "
        "--storage-path=/var/lib/parca",
    )
    assert_healthy(state_out_2)


def test_profiling_endpoint_relation(context, base_state):
    self_profiling_relation = Relation(
        "self-profiling-endpoint",
    )

    relation = Relation(
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
            "relabel_configs": RELABEL_CONFIG,
        }
    ]
    with context(
        context.on.relation_changed(relation),
        replace(base_state, relations={relation, self_profiling_relation}),
    ) as mgr:
        assert mgr.charm.profiling_consumer.jobs() == expected_jobs
        state_out = mgr.run()
    expected_config = {
        "object_storage": {
            "bucket": {"config": {"directory": "/var/lib/parca"}, "type": "FILESYSTEM"}
        },
        "scrape_configs": expected_jobs,
    }
    assert_parca_config_equals(context, state_out, expected_config)


def test_metrics_endpoint_relation(context, base_state):
    # Create a relation to an app named "prometheus"
    relation = Relation("metrics-endpoint", remote_app_name="prometheus")

    state_out = context.run(
        context.on.relation_joined(relation),
        replace(base_state, leader=True, relations={relation}),
    )

    # Grab the unit data from the relation
    rel_out = state_out.get_relation(relation.id)
    # Ensure that the unit set its targets correctly
    expected = {
        "prometheus_scrape_unit_address": socket.getfqdn(),
        "prometheus_scrape_unit_name": "parca-k8s/0",
    }
    for key, val in expected.items():
        assert rel_out.local_unit_data[key] == val


def test_parca_store_relation(context, base_state):
    # Create a relation to an app named "parca-store-endpoint"
    relation = Relation("parca-store-endpoint", remote_app_name="foo")

    state_out = context.run(
        context.on.relation_joined(relation),
        replace(base_state, leader=True, relations={relation}),
    )

    # Grab the unit data from the relation
    rel_out = state_out.get_relation(relation.id)
    # Ensure that the unit set its targets correctly
    expected = {
        "remote-store-address": f"{socket.getfqdn()}:{NGINX_PORT}",
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

    relation = Relation(
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
    assert_parca_command_equals(
        state_out,
        "/parca "
        f"--config-path={DEFAULT_CONFIG_PATH} "
        f"--http-address=localhost:{PARCA_PORT} "
        "--storage-enable-wal "
        "--storage-active-memory=4294967296 "
        "--store-address=grpc.polarsignals.com:443 "
        "--bearer-token=deadbeef "
        "--insecure=false "
        "--mode=scraper-only",
    )


def test_self_profiling_no_endpoint_relation(context, base_state):
    # verify that the scrape config contains the self-scraping job
    expected_scrape_config = [
        {
            "job_name": "parca",
            "relabel_configs": RELABEL_CONFIG,
            "static_configs": [
                {
                    "targets": [f"{socket.getfqdn()}:{NGINX_PORT}"],
                }
            ],
        }
    ]

    with context(context.on.config_changed(), base_state) as mgr:
        scrape_config = mgr.charm._profiling_scrape_configs
        assert scrape_config == expected_scrape_config


def test_self_profiling_endpoint_relation(context, base_state):
    expected_scrape_jobs = [
        {"static_configs": [{"targets": [f"{socket.getfqdn()}:{NGINX_PORT}"]}]}
    ]
    # GIVEN a self-profiling-endpoint relation
    relation = Relation("self-profiling-endpoint")

    # WHEN we get a relation changed event
    with context(
        context.on.relation_changed(relation),
        replace(base_state, leader=True, relations={relation}),
    ) as mgr:
        state_out = mgr.run()
        scrape_config = mgr.charm._profiling_scrape_configs
        # THEN no self-profiling scrape job in the generated config
        assert not scrape_config

        # AND self-profiling scrape job is sent to remote app
        rel_out = state_out.get_relation(relation.id)
        assert rel_out.local_app_data["scrape_jobs"] == json.dumps(expected_scrape_jobs)


def test_parca_workload_tracing_relation(context, base_state):
    remote_app_databag = {
        "receivers": json.dumps(
            [{"protocol": {"name": "otlp_grpc", "type": "grpc"}, "url": "192.0.2.0/24"}]
        )
    }
    relation = Relation(
        "workload-tracing", remote_app_name="backend", remote_app_data=remote_app_databag
    )

    state_out = context.run(
        context.on.relation_changed(relation),
        replace(base_state, leader=True, relations={relation}),
    )

    # Check the Parca is started with the correct command including the tracing flag
    assert_parca_command_equals(
        state_out,
        f"/parca "
        f"--config-path={DEFAULT_CONFIG_PATH} "
        f"--http-address=localhost:{PARCA_PORT} "
        "--storage-enable-wal "
        "--storage-active-memory=4294967296 "
        "--otlp-address=192.0.2.0/24",
    )
