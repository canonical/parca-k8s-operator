# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging
import shlex
import subprocess
from subprocess import getoutput, getstatusoutput
from typing import List, Tuple

import jubilant
from jubilant import Juju

from nginx import CA_CERT_PATH, Nginx

PARCA = "parca"
INTEGRATION_TESTERS_CHANNEL = "2/edge"
TESTING_MINIO_ACCESS_KEY = "accesskey"
TESTING_MINIO_SECRET_KEY = "secretkey"
MINIO = "minio"
S3_INTEGRATOR = "s3-integrator"
S3_INTEGRATOR_CHANNEL = "2/edge"
BUCKET_NAME = "parca"
ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
logger= logging.getLogger("helpers")


def get_unit_ip(model_name, app_name, unit_id):
    """Return a juju unit's IP."""
    return getoutput(
        f"""juju status --model {model_name} --format json | jq '.applications.{app_name}.units."{app_name}/{unit_id}".address'"""
    ).strip('"')


def get_unit_fqdn(model_name, app_name, unit_id):
    """Return a juju unit's K8s cluster FQDN."""
    return f"{app_name}-{unit_id}.{app_name}-endpoints.{model_name}.svc.cluster.local"

def _deploy_and_configure_minio(juju: Juju):
    if not juju.status().apps.get(MINIO):
        juju.deploy(
            MINIO,
            channel="edge",
            trust=True,
            config={
                "access-key": ACCESS_KEY,
                "secret-key": SECRET_KEY,
            }
        )
    juju.wait(
        lambda status: status.apps[MINIO].is_active,
        error=jubilant.any_error,
        delay=5,
        successes=3,
        timeout=2000,
    )

def deploy_s3(juju, bucket_name: str, s3_integrator_app: str=S3_INTEGRATOR):
    """Deploy minio and s3-integrator using track 2 approach.

    Track 2 of s3-integrator uses Juju secrets (secret URI in 'credentials' config).
    Unlike track 1, it auto-creates buckets so manual provisioning is not needed.
    """
    _deploy_and_configure_minio(juju)

    logger.info(f"deploying {s3_integrator_app=}")
    juju.deploy(
        "s3-integrator", s3_integrator_app, channel=S3_INTEGRATOR_CHANNEL
    )

    logger.info("configuring s3 integrator with track 2 approach...")

    # Track 2: Create and grant Juju secret with credentials
    secret_uri = juju.cli(
        "add-secret",
        f"{s3_integrator_app}-creds",
        f"access-key={ACCESS_KEY}",
        f"secret-key={SECRET_KEY}",
    ).strip()

    logger.info(f"Created secret: {secret_uri}")
    juju.cli("grant-secret", secret_uri, s3_integrator_app)

    # Configure s3-integrator with endpoint, bucket, AND credentials secret URI
    # Track 2 DOES use 'credentials' config but expects a Juju secret URI
    juju.config(
        s3_integrator_app,
        {
            "endpoint": f"minio-0.minio-endpoints.{juju.model}.svc.cluster.local:9000",
            "bucket": bucket_name,
            "credentials": secret_uri,
        },
    )


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


