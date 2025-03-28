# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging
import shlex
import subprocess
from subprocess import getoutput, getstatusoutput
from typing import List, Tuple

from minio import Minio
from pytest_operator.plugin import OpsTest

from nginx import CA_CERT_PATH, Nginx

PARCA = "parca"

TESTING_MINIO_ACCESS_KEY = "accesskey"
TESTING_MINIO_SECRET_KEY = "secretkey"
MINIO = "minio"
S3_INTEGRATOR = "s3-integrator"
BUCKET_NAME = "parca"


def get_unit_ip(model_name, app_name, unit_id):
    """Return a juju unit's IP."""
    return getoutput(
        f"""juju status --model {model_name} --format json | jq '.applications.{app_name}.units."{app_name}/{unit_id}".address'"""
    ).strip('"')


def get_unit_fqdn(model_name, app_name, unit_id):
    """Return a juju unit's K8s cluster FQDN."""
    return f"{app_name}-{unit_id}.{app_name}-endpoints.{model_name}.svc.cluster.local"


async def deploy_s3(ops_test: OpsTest, app="parca"):
    await ops_test.model.deploy(S3_INTEGRATOR, channel="edge")

    await ops_test.model.integrate(app + ":s3", S3_INTEGRATOR + ":s3-credentials")

    await deploy_and_configure_minio(ops_test)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[app, S3_INTEGRATOR],
            status="active",
            timeout=2000,
            idle_period=30,
        )


async def deploy_and_configure_minio(ops_test: OpsTest):
    config = {
        "access-key": TESTING_MINIO_ACCESS_KEY,
        "secret-key": TESTING_MINIO_SECRET_KEY,
    }
    await ops_test.model.deploy(MINIO, channel="edge", trust=True, config=config)
    await ops_test.model.wait_for_idle(apps=[MINIO], status="active", timeout=2000)
    minio_addr = get_unit_ip(ops_test.model.name, MINIO, 0)

    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key="accesskey",
        secret_key="secretkey",
        secure=False,
    )

    # create tempo bucket
    found = mc_client.bucket_exists(BUCKET_NAME)
    if not found:
        mc_client.make_bucket(BUCKET_NAME)

    # configure s3-integrator
    s3_integrator_app = ops_test.model.applications[S3_INTEGRATOR]
    s3_integrator_leader = s3_integrator_app.units[0]

    await s3_integrator_app.set_config(
        {
            "endpoint": f"minio-0.minio-endpoints.{ops_test.model.name}.svc.cluster.local:9000",
            "bucket": BUCKET_NAME,
        }
    )

    action = await s3_integrator_leader.run_action("sync-s3-credentials", **config)
    action_result = await action.wait()
    assert action_result.status == "completed"


async def get_public_address(ops_test: OpsTest, app_name):
    """Return a juju application's public address."""
    status = await ops_test.model.get_status()  # noqa: F821
    return status["applications"][app_name]["public-address"]


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


def query_label_values(
        model_name, app_name=PARCA
) -> List[str]:
    """Query the parca.query.v1alpha1.QueryService/Values service with grpcurl."""
    unit_ip = get_unit_ip(model_name, app_name, 0)
    url = f"{unit_ip}:{Nginx.parca_grpc_server_port}"
    service = "parca.query.v1alpha1.QueryService/Values"
    query = "-d '{\"label_name\": \"juju_unit\"}'"

    # at the moment passing a file cacert isn't supported by the grpcurl snap: hence -insecure
    cmd = f"grpcurl -insecure {query} {url} {service}"
    logging.debug(f"calling: {cmd!r}")
    proc = subprocess.run(shlex.split(cmd), text=True, capture_output=True)
    proc.check_returncode()
    return json.loads(proc.stdout).get("labelValues", [])


if __name__ == '__main__':
    print(query_label_values("test-tls-zy31"))