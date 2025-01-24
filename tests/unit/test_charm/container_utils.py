from pathlib import Path

import yaml
from charms.parca_k8s.v0.parca_config import DEFAULT_CONFIG_PATH
from scenario import Context, State

from parca import DEFAULT_CONFIG_PATH


def assert_parca_command_equals(state: State, expected_command):
    """Assert that the command line for the parca service in the parca container matches."""
    container = state.get_container("parca")
    assert container.plan.services["parca"].command == expected_command


def assert_parca_config_equals(context: Context, state: State, expected_config):
    """Assert that the parca config file in the container matches."""
    container = state.get_container("parca")

    config = container.get_filesystem(context).joinpath(Path(DEFAULT_CONFIG_PATH).relative_to("/"))
    assert yaml.safe_load(config.read_text()) == expected_config
