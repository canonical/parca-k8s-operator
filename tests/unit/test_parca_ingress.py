import json
from unittest.mock import patch

import pytest
from charms.parca_k8s.v0.parca_config import parca_command_line
from ops.pebble import Layer
from ops.testing import Container, Context, Relation, State

from charm import ParcaOperatorCharm

DEFAULT_CONFIG = {"enable-persistence": False, "memory-storage-limit": 1024}


@pytest.fixture(autouse=True, scope="module")
def patch_version():
    with patch("parca.Parca._fetch_version", lambda _: "0.0.42"):
        yield


@pytest.mark.parametrize("path", ("fubar", "livingbeef"))
def test_prefix_ingress_pebble_ready(path):
    # GIVEN a parca charm with an ingress relation
    ctx = Context(ParcaOperatorCharm)

    container = Container("parca", can_connect=True)
    state = State(
        containers={container},
        relations={
            Relation(
                "ingress",
                remote_app_data={"ingress": json.dumps({"url": f"http://example.com/{path}"})},
            )
        },
    )

    # WHEN we process a pebble-ready
    state_out = ctx.run(ctx.on.pebble_ready(container), state)

    # THEN the plan's command contains a path-prefix arg
    command = state_out.get_container("parca").plan.services["parca"].command.split()
    assert f"--path-prefix='/{path}'" in command


@pytest.mark.parametrize("path", ("fubar", None))
def test_no_prefix_ingress_broken(path):
    # GIVEN a parca charm without an ingress relation,
    #   regardless of the prefix previously in the command
    ctx = Context(ParcaOperatorCharm)
    container = Container(
        "parca",
        can_connect=True,
        layers={
            "parca": Layer(
                {
                    "override": "replace",
                    "summary": "parca",
                    "command": parca_command_line(DEFAULT_CONFIG, path_prefix=None),
                    "startup": "enabled",
                }
            )
        },
    )

    ingress = Relation("ingress")
    state = State(relations={ingress}, containers={container})

    # WHEN we process a relation-broken
    state_out = ctx.run(ctx.on.relation_broken(ingress), state)

    # THEN the plan's command is updated to contain no path-prefix arg
    command = state_out.get_container("parca").plan.services["parca"].command
    assert "--path-prefix" not in command


@pytest.mark.parametrize("path", ("fubar", None))
@pytest.mark.parametrize("new_path", ("baaz", "quzzified"))
def test_prefix_ingress_created(path, new_path):
    # GIVEN a parca charm with an ingress relation,
    #   regardless of the prefix previously in the command
    ctx = Context(ParcaOperatorCharm)

    container = Container(
        "parca",
        can_connect=True,
        layers={
            "parca": Layer(
                {
                    "override": "replace",
                    "summary": "parca",
                    "command": parca_command_line(DEFAULT_CONFIG, path_prefix=None),
                    "startup": "enabled",
                }
            )
        },
    )

    ingress = Relation(
        "ingress",
        remote_app_data={"ingress": json.dumps({"url": f"http://example.com/{new_path}"})},
    )
    state = State(relations={ingress}, containers={container})

    # WHEN we process a relation-broken
    state_out = ctx.run(ctx.on.relation_created(ingress), state)

    # THEN the plan's command is updated to contain the right path-prefix arg
    command = state_out.get_container("parca").plan.services["parca"].command
    assert f"--path-prefix='/{new_path}'" in command
