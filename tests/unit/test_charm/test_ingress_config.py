import json

import pytest
from ops.pebble import Layer
from ops.testing import Container, Context, Relation, State

from charm import ParcaOperatorCharm
from parca import parca_command_line

DEFAULT_CONFIG = {"enable-persistence": False, "memory-storage-limit": 1024}


@pytest.mark.parametrize("path", ("fubar", "livingbeef"))
def test_prefix_ingress_pebble_ready(
    path, parca_container, nginx_container, nginx_prometheus_exporter_container
):
    # GIVEN a parca charm with an ingress relation
    context = Context(ParcaOperatorCharm)

    state = State(
        leader=True,
        containers={parca_container, nginx_container, nginx_prometheus_exporter_container},
        relations={
            Relation(
                "ingress",
                remote_app_data={"external_host": f"example.com",
                                             "scheme": "http"},
            )
        },
    )

    # WHEN we process a pebble-ready
    state_out = context.run(context.on.pebble_ready(parca_container), state)

    # THEN the plan's command contains a path-prefix arg
    command = state_out.get_container("parca").plan.services["parca"].command.split()
    assert f"--path-prefix='/{path}'" in command


@pytest.mark.parametrize("path", ("fubar", None))
def test_no_prefix_ingress_broken(
    path, context, nginx_container, nginx_prometheus_exporter_container
):
    # GIVEN a parca charm without an ingress relation,
    #   regardless of the prefix previously in the command
    container = Container(
        "parca",
        can_connect=True,
        layers={
            "parca": Layer(
                {
                    "override": "replace",
                    "summary": "parca",
                    "command": parca_command_line("7070", DEFAULT_CONFIG, path_prefix=None),
                    "startup": "enabled",
                }
            )
        },
    )

    ingress = Relation("ingress")
    state = State(
        relations={ingress},
        containers={container, nginx_container, nginx_prometheus_exporter_container},
    )

    # WHEN we process a relation-broken
    state_out = context.run(context.on.relation_broken(ingress), state)

    # THEN the plan's command is updated to contain no path-prefix arg
    command = state_out.get_container("parca").plan.services["parca"].command
    assert "--path-prefix" not in command


@pytest.mark.parametrize("path", ("fubar", None))
@pytest.mark.parametrize("new_path", ("baaz", "quzzified"))
def test_prefix_ingress_created(
    path, new_path, context, nginx_container, nginx_prometheus_exporter_container
):
    # GIVEN a parca charm with an ingress relation,
    #   regardless of the prefix previously in the command
    container = Container(
        "parca",
        can_connect=True,
        layers={
            "parca": Layer(
                {
                    "override": "replace",
                    "summary": "parca",
                    "command": parca_command_line("7070", DEFAULT_CONFIG, path_prefix=None),
                    "startup": "enabled",
                }
            )
        },
    )

    ingress = Relation(
        "ingress",
        remote_app_data={"ingress": json.dumps({"url": f"http://example.com/{new_path}"})},
    )
    state = State(
        relations={ingress},
        containers={container, nginx_container, nginx_prometheus_exporter_container},
    )

    # WHEN we process a relation-broken
    state_out = context.run(context.on.relation_created(ingress), state)

    # THEN the plan's command is updated to contain the right path-prefix arg
    command = state_out.get_container("parca").plan.services["parca"].command
    assert f"--path-prefix='/{new_path}'" in command
