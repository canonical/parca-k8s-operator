
import pytest
from ops.pebble import Layer
from ops.testing import Container, Relation, State

from parca import parca_command_line

DEFAULT_CONFIG = {"enable-persistence": False, "memory-storage-limit": 1024}


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
                    "command": parca_command_line("7070", DEFAULT_CONFIG),
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

    # THEN the plan's command is present
    assert state_out.get_container("parca").plan.services["parca"].command

