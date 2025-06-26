from typing import List, Set, cast

import jubilant
import pytest
import requests
from jubilant import Juju
from minio import Minio
from tenacity import retry, stop_after_attempt, wait_fixed

from tests.integration.helpers import (
    ACCESS_KEY,
    PARCA,
    S3_CREDENTIALS,
    SECRET_KEY,
    get_app_ip_address,
    get_unit_ip_address,
)

MINIO="minio"

TEMPO = "tempo"
TEMPO_WORKER = "tempo-worker"
TEMPO_S3 = "tempo-s3" # integrator

def deploy_monolithic_tempo_cluster(
    juju: Juju,
    worker_app_name: str,
    s3_app_name: str,
    coordinator_app_name,
    bucket_name: str,
        channel:str="2/edge"
):
    """Deploy a tempo-monolithic cluster."""
    # worker and coordinator
    juju.deploy(
        "tempo-worker-k8s",
        app=worker_app_name,
        channel=channel,
        trust=True,
    )
    juju.deploy(
        "tempo-coordinator-k8s",
        app=coordinator_app_name,
        channel=channel,
        trust=True,
    )
    juju.integrate(coordinator_app_name, worker_app_name)

    # s3 integrator
    juju.deploy("s3-integrator", s3_app_name, channel="edge", config={
            "endpoint": f"{MINIO}-0.minio-endpoints.{juju.model}.svc.cluster.local:9000",
            "bucket": bucket_name,
        })
    juju.integrate(coordinator_app_name + ":s3", s3_app_name + ":s3-credentials")

    # s3 backend
    juju.deploy("minio", MINIO, channel="edge", trust=True, config=S3_CREDENTIALS)

    # wait for minio to be ready, because we need to create the bucket manually
    juju.wait(
        lambda status: status.apps[MINIO].is_active,
        error=jubilant.any_error,
    )

    minio_addr = get_unit_ip_address(juju, MINIO, 0)
    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )
    # create bucket
    found = mc_client.bucket_exists(bucket_name)
    if not found:
        mc_client.make_bucket(bucket_name)

    # sync credentials
    task = juju.run(s3_app_name + "/0", "sync-s3-credentials", params=S3_CREDENTIALS)
    assert task.status == "completed"

    # wait for all active
    juju.wait(
        lambda status: jubilant.all_active(status, coordinator_app_name, worker_app_name, s3_app_name),
        timeout=2000,
    )


@pytest.mark.setup
def test_deploy_tempo_stack_monolithic(juju: Juju):
    # deploy a tempo stack in monolithic mode
    deploy_monolithic_tempo_cluster(
        juju,
        worker_app_name=TEMPO_WORKER,
        s3_app_name=TEMPO_S3,
        coordinator_app_name=TEMPO,
        bucket_name="tempo"
    )

    # send the charm and workload traces from `this stack` to the remote one
    juju.integrate(PARCA + ":charm-tracing", TEMPO + ":tracing")
    juju.integrate(PARCA + ":workload-tracing", TEMPO + ":tracing")
    juju.wait(
        lambda status: all(
            status.apps[app].is_active
            for app in [PARCA, TEMPO, TEMPO_WORKER]
        ),
        timeout=1000,
    )


def get_ingested_traces_service_names(tempo_host, tls: bool) -> Set[str]:
    """Fetch all ingested traces tags."""
    url = f"{'https' if tls else 'http'}://{tempo_host}:3200/api/search/tag/service.name/values"
    req = requests.get(
        url,
        verify=False,
    )
    assert req.status_code == 200, req.reason
    tags = cast(List[str], req.json()["tagValues"])
    return set(tags)


@retry(stop=stop_after_attempt(10), wait=wait_fixed(10))
def test_verify_charm_tracing(juju: Juju):
    # adjust update-status interval to generate a charm tracing span faster
    juju.cli("model-config", "update-status-hook-interval=5s")

    services = get_ingested_traces_service_names(
        get_app_ip_address(juju, TEMPO), tls=False
    )
    assert PARCA in services

    # adjust back to the default interval time
    juju.cli("model-config", "update-status-hook-interval=5m")


@retry(stop=stop_after_attempt(10), wait=wait_fixed(10))
def test_verify_workload_tracing(juju: Juju):
    services = get_ingested_traces_service_names(
        get_app_ip_address(juju, TEMPO), tls=False
    )
    assert PARCA in services
