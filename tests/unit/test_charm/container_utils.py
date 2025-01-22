from pathlib import Path

import yaml
from scenario import Context, State

from parca import DEFAULT_CONFIG_PATH


def assert_parca_command_equals(state: State, expected_command):
    """Assert that the command line for the parca service in the parca container matches."""
    container = state.get_container("parca")
    assert container.plan.services["parca"].command == expected_command


