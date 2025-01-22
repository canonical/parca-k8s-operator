# Copyright 2022 Jon Seager
# See LICENSE file for licensing details.

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from ops.model import ActiveStatus, WaitingStatus
from ops.testing import CharmEvents, Relation, State, Container, Context

from charms.parca_k8s.v0.parca_config import DEFAULT_CONFIG_PATH
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


@pytest.mark.parametrize(
    "container_name",
    (
            'parca', 'nginx', 'nginx-prometheus-exporter'
    ),
)
def test_healthy_container_events(context, container_name, base_state):
    event = context.on.pebble_ready(base_state.get_container(container_name))
    state_out = context.run(event, base_state)
    assert_healthy(state_out)


@pytest.mark.parametrize(
    "ready, not_ready",
    (
            (('parca', 'nginx',), ('nginx-prometheus-exporter',)),
            (('parca', 'nginx-prometheus-exporter'), ('nginx',)),
            (('nginx-prometheus-exporter', 'nginx'), ('parca',)),
    ),
)
@pytest.mark.parametrize(
    "event",
    (
            CharmEvents().update_status(),
            CharmEvents().start(),
            CharmEvents().install(),
            CharmEvents().config_changed(),
    ),
)
def test_waiting_containers_not_ready(
        context, ready, not_ready, base_state, event
):
    state = replace(base_state,
        containers={
            *{Container(name, can_connect=True) for name in ready},
            *{Container(name, can_connect=False) for name in not_ready},
        }
    )
    state_out = context.run(event, state)
    assert state_out.unit_status == WaitingStatus(f"Waiting for containers: {list(not_ready)}...")


def assert_parca_config_exists(context: Context, state: State):
    """Assert that the parca config file in the container exists and is valid yaml."""
    container = state.get_container("parca")

    config = container.get_filesystem(context).joinpath(Path(DEFAULT_CONFIG_PATH).relative_to("/"))
    assert yaml.safe_load(config.read_text())

@pytest.mark.parametrize(
    "event",
    (
            CharmEvents().update_status(),
            CharmEvents().start(),
            CharmEvents().install(),
            CharmEvents().config_changed(),
    ),
)
def test_config_file_written(context, event, base_state):
    state_out = context.run(event, base_state)
    assert_parca_config_exists(
        context,
        state_out,
    )


def test_profiling_endpoint_relation(context, base_state):
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

    assert_parca_config_exists(context, state_out)


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
        "prometheus_scrape_unit_address": "192.0.2.0",
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
