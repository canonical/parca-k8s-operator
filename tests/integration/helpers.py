# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from subprocess import getoutput, getstatusoutput

import requests
from juju.application import Application
from juju.unit import Unit
from minio import Minio
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_attempt, wait_exponential

from nginx import CA_CERT_PATH, NGINX_PORT

PARCA = "parca"

from minio import Minio
from pytest_operator.plugin import OpsTest

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

async def deploy_tempo_cluster(ops_test: OpsTest):
    """Deploys tempo in its HA version together with minio and s3-integrator."""
    tempo_app = "tempo"
    worker_app = "tempo-worker"
    tempo_worker_charm_url, worker_channel = "tempo-worker-k8s", "edge"
    tempo_coordinator_charm_url, coordinator_channel = "tempo-coordinator-k8s", "edge"
    await ops_test.model.deploy(
        tempo_worker_charm_url, application_name=worker_app, channel=worker_channel, trust=True
    )
    await ops_test.model.deploy(
        tempo_coordinator_charm_url,
        application_name=tempo_app,
        channel=coordinator_channel,
        trust=True,
    )
    await ops_test.model.deploy("s3-integrator", channel="edge")

    await ops_test.model.integrate(tempo_app + ":s3", "s3-integrator" + ":s3-credentials")
    await ops_test.model.integrate(tempo_app + ":tempo-cluster", worker_app + ":tempo-cluster")

    await deploy_and_configure_minio(ops_test)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[tempo_app, worker_app, "s3-integrator"],
            status="active",
            timeout=2000,
            idle_period=30,
            raise_on_error=False,
        )

async def get_pubic_address(ops_test: OpsTest, app_name):
    """Return a juju application's public address."""
    status = await ops_test.model.get_status()  # noqa: F821
    return status["applications"][app_name]["public-address"]

@retry(stop=stop_after_attempt(15), wait=wait_exponential(multiplier=1, min=4, max=10))
async def get_traces(tempo_host: str, service_name="tracegen-otlp_http", tls=True):
    """Get traces directly from Tempo REST API."""
    url = f"{'https' if tls else 'http'}://{tempo_host}:3200/api/search?tags=service.name={service_name}"
    req = requests.get(
        url,
        verify=False,
    )
    assert req.status_code == 200
    traces = json.loads(req.text)["traces"]
    assert len(traces) > 0
    return traces


def query_parca_server(
    model_name, exec_target_app_name, tls=False, ca_cert_path=CA_CERT_PATH, url_path=""
):
    """Run a query the parca server."""
    parca_address = get_unit_fqdn(model_name, PARCA, 0)
    url = f"{'https' if tls else 'http'}://{parca_address}:{NGINX_PORT}{url_path}"
    # Parca's certificate only contains the fqdn address of parca as SANs.
    # To query the parca server with TLS while validating the certificate, we need to perform the query
    # against the parca server's fqdn.
    # We can do that from inside another K8s pod, such as ssc.
    cert_flags = f"--cacert' {ca_cert_path}" if tls else ""
    cmd = f"""juju exec --model {model_name} --unit {exec_target_app_name}/0 "curl {cert_flags} {url}" """
    print(cmd)
    return getstatusoutput(cmd)
