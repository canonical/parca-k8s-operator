# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from subprocess import getoutput


def get_unit_ip(model_name, app_name, unit_id):
    """Return a juju unit's IP."""
    return getoutput(
        f"""juju status --model {model_name} --format json | jq '.applications.{app_name}.units."{app_name}/{unit_id}".address'"""
    ).strip('"')


def get_unit_fqdn(model_name, app_name, unit_id):
    """Return a juju unit's K8s cluster FQDN."""
    return f"{app_name}-{unit_id}.{app_name}-endpoints.{model_name}.svc.cluster.local"
