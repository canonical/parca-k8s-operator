from dataclasses import replace

import pytest
import yaml
from ops.testing import Relation, State


@pytest.fixture
def base_state(parca_container, nginx_container, nginx_prometheus_exporter_container, parca_peers):
    return State(
        containers={parca_container, nginx_container, nginx_prometheus_exporter_container},
        relations={parca_peers},
    )


@pytest.mark.parametrize(
    "objective",
    (99.9, 1.),
)
def test_get_slo_spec(context, base_state, objective):
    """Test that _get_slo_spec returns valid YAML for each objective."""
    with context(
            context.on.config_changed(),
            replace(base_state, config={"slo-errors-target": objective, "slo-latency-target": objective}),
    ) as mgr:
        spec = mgr.charm._get_slo_spec()

        # Check that the spec is not empty
        assert spec

        # Check that it's valid YAML
        import yaml
        spec_dict = yaml.safe_load(spec)

        # Check that it has the expected structure
        assert spec_dict["version"] == "prometheus/v1"
        assert spec_dict["service"] == "parca"
        assert "slos" in spec_dict
        assert len(spec_dict["slos"]) == 16  # 16 SLOs total


@pytest.mark.parametrize("objectives", [(99., 97.), (1., 23.)])
def test_slos_relation_sends_spec(context, base_state, objectives):
    """Test that SLO spec is sent when slos relation changes."""
    error_obj, latency_obj = objectives
    relation = Relation(endpoint="slos", remote_app_name="sloth")

    state_out = context.run(
        context.on.relation_changed(relation),
        replace(base_state, leader=True, relations={relation}, config={"slo-errors-target": error_obj,
                                                                       "slo-latency-target": latency_obj}),
    )

    # Check that the relation has data
    rel_out = state_out.get_relation(relation.id)
    # The SlothProvider stores data as a YAML-encoded list in the "slos" field
    assert "slos" in rel_out.local_app_data

    # Verify it's valid YAML containing a list of SLO specs
    slo_list = yaml.safe_load(rel_out.local_app_data["slos"])
    assert isinstance(slo_list, list)
    assert len(slo_list) > 0
    # Each item in the list should be a complete SLO spec
    assert slo_list[0]["service"] == "parca"

    for slo in slo_list[0]["slos"]:
        if "error" in slo["name"]:
            assert slo["objective"] == error_obj
        elif "latency" in slo["name"]:
            assert slo["objective"] == latency_obj


@pytest.mark.parametrize("objectives", [(99., 97.), (1., 23.)])
def test_slos_relation_config_changed(context, base_state, objectives):
    """Test that SLO spec is updated when config changes."""
    error_obj, latency_obj = objectives
    relation = Relation(endpoint="slos", remote_app_name="sloth")

    state_out = context.run(
        context.on.config_changed(),
        replace(base_state, leader=True, relations={relation}, config={"slo-errors-target": error_obj,
                                                                       "slo-latency-target": latency_obj}),
    )

    rel_out = state_out.get_relation(relation.id)
    assert "slos" in rel_out.local_app_data

    slo_list = yaml.safe_load(rel_out.local_app_data["slos"])
    assert isinstance(slo_list, list)

    # The slo_list is a list containing one SLO document, which has a "slos" array
    for slo in slo_list[0]["slos"]:
        if "error" in slo["name"]:
            assert slo["objective"] == error_obj
        elif "latency" in slo["name"]:
            assert slo["objective"] == latency_obj


def test_slos_config_precedence(context, base_state):
    """Test that the "spec" config option takes precedence over manual targets."""
    relation = Relation(endpoint="slos", remote_app_name="sloth")
    test_value = """version: prometheus/v1
service: parca
slos:
- name: random garbage
  objective: ERROR_OBJECTIVE
  description: random garbage
  sli:
    events:
      error_query: grpc_client_handled_total{job="random garbage"}
      total_query: grpc_client_handled_total{job="random garbage"}
"""

    state_out = context.run(
        context.on.config_changed(),
        replace(base_state, leader=True, relations={relation}, config={
            "slo-errors-target": 50.,
            "slo-latency-target": 50.,
            "slos": test_value
        }),
    )

    rel_out = state_out.get_relation(relation.id)
    assert "slos" in rel_out.local_app_data

    assert "random garbage" in rel_out.local_app_data["slos"]
