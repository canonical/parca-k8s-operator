# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging
import shlex
import subprocess
from pathlib import Path
from subprocess import getoutput, getstatusoutput
from typing import List, Tuple

import yaml
from jubilant import Juju

from nginx import CA_CERT_PATH, Nginx

PARCA = "parca"
S3_APP = "s3-app"
INTEGRATION_TESTERS_CHANNEL = "dev/edge"
logger= logging.getLogger("helpers")


def get_unit_ip(model_name, app_name, unit_id):
    """Return a juju unit's IP."""
    return getoutput(
        f"""juju status --model {model_name} --format json | jq '.applications.{app_name}.units."{app_name}/{unit_id}".address'"""
    ).strip('"')


def get_unit_fqdn(model_name, app_name, unit_id):
    """Return a juju unit's K8s cluster FQDN."""
    return f"{app_name}-{unit_id}.{app_name}-endpoints.{model_name}.svc.cluster.local"


def get_app_ip_address(juju: Juju, app_name):
    """Return a juju application's IP address."""
    return juju.status().apps[app_name].address


def get_unit_ip_address(juju: Juju, app_name: str, unit_no: int):
    """Return a juju unit's IP address."""
    return juju.status().apps[app_name].units[f"{app_name}/{unit_no}"].address

def query_parca_server(
        model_name, exec_target_app_name, tls=False, ca_cert_path=CA_CERT_PATH, url_path=""
) -> Tuple[int, str]:
    """Curl the parca server from a juju unit, and return the statuscode."""
    parca_address = get_unit_fqdn(model_name, PARCA, 0)
    url = f"{'https' if tls else 'http'}://{parca_address}:{Nginx.parca_http_server_port}{url_path}"
    # Parca's certificate only contains the fqdn address of parca as SANs.
    # To query the parca server with TLS while validating the certificate, we need to perform the query
    # against the parca server's fqdn.
    # We can do that from inside another K8s pod, such as ssc.
    cert_flags = f"--cacert {ca_cert_path}" if tls else ""
    cmd = f"""juju exec --model {model_name} --unit {exec_target_app_name}/0 "curl {cert_flags} {url}" """
    return getstatusoutput(cmd)


def get_parca_ingested_label_values(
        model_name, app_name=PARCA, label:str = "juju_application", tls:bool=False
) -> List[str]:
    """Query the parca.query.v1alpha1.QueryService/Values service with grpcurl."""
    unit_ip = get_unit_ip(model_name, app_name, 0)
    url = f"{unit_ip}:{Nginx.parca_grpc_server_port}"
    service = "parca.query.v1alpha1.QueryService/Values"
    query = f"-d '{{\"label_name\": \"{label}\"}}'"

    # at the moment passing a file cacert isn't supported by the grpcurl snap: hence -plaintext
    # if TLS is active, switch this to -insecure
    insecure_flag = "-insecure" if tls else "-plaintext"
    cmd = f"grpcurl {insecure_flag} {query} {url} {service}"
    logger.debug(f"calling: {cmd!r}")
    proc = subprocess.run(shlex.split(cmd), text=True, capture_output=True)
    proc.check_returncode()
    return json.loads(proc.stdout).get("labelValues", [])


def get_resources(root: Path | str = "./") -> dict[str, str] | None:
    """Obtain charm resources from metadata.yaml or charmcraft.yaml upstream-source fields."""
    for meta_name in ("metadata.yaml", "charmcraft.yaml"):
        meta_path = Path(root) / meta_name
        if meta_path.exists():
            meta = yaml.safe_load(meta_path.read_text())
            if meta_resources := meta.get("resources"):
                return {
                    resource: res_meta["upstream-source"]
                    for resource, res_meta in meta_resources.items()
                }
            logger.info("resources not found in %s; proceeding without resources", meta_name)
            return None
    logger.error("metadata/charmcraft.yaml not found at %s; unable to load resources", root)
    return None


def pack(root: Path | str = "./", platform: str | None = None) -> Path:
    """Pack a local charm and return the path to the packed .charm file."""
    platform_arg = f" --platform {platform}" if platform else ""
    cmd = f"charmcraft pack -p {root}{platform_arg}"
    proc = subprocess.run(
        shlex.split(cmd),
        check=True,
        capture_output=True,
        text=True,
    )
    # charmcraft prints "Packed <filename>" lines to stderr
    packed_charms = [
        line.split()[1] for line in proc.stderr.strip().splitlines() if line.startswith("Packed")
    ]
    if not packed_charms:
        raise ValueError(
            f"unable to get packed charm(s) ({cmd!r} completed with "
            f"{proc.returncode=}, {proc.stdout=}, {proc.stderr=})"
        )
    if len(packed_charms) > 1:
        raise ValueError(
            "This charm supports multiple platforms. "
            "Pass a `platform` argument to control which charm you're getting instead."
        )
    return Path(packed_charms[0]).resolve()
