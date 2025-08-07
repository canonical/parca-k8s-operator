import json

import pytest
from ops import testing


@pytest.mark.parametrize("hostname, port", (
        ("foo.com", 883),
        ("bar.com", 42),
))
@pytest.mark.parametrize("remote_app_name",
                         ("something", "else"),
                         )
@pytest.mark.parametrize("remote_unit_id",
                         (1, 12),
                         )
@pytest.mark.verifies(feature="profiling")
def test_scrape_config(context, hostname, port, remote_app_name, remote_unit_id):
    state = testing.State(
        leader=True,
        containers={
            testing.Container("nginx"),
            testing.Container("nginx-prometheus-exporter"),
            testing.Container("parca"),
        },
        relations={
            testing.Relation(
                "profiling-endpoint",
                remote_app_name=remote_app_name,
                remote_app_data={

                    "scrape_jobs": json.dumps([{"static_configs": [{
                        "targets":
                            [f"*:{port}"]}]}
                    ]),
                    "scrape_metadata": json.dumps({
                        "model": "baz",
                        "model_uuid": "3cdca972-b819-4f03-8d90-9c7a36d234c9",
                        "application": remote_app_name,
                        "unit": f"{remote_app_name}/{remote_unit_id}",
                        "charm_name": "parca-k8s"})

                },
                remote_units_data={
                    remote_unit_id:
                        {
                            "parca_scrape_unit_address": json.dumps(hostname),
                            "parca_scrape_unit_fname": json.dumps(f"{remote_app_name}/{remote_unit_id}")
                        }
                })
        })

    with context(context.on.update_status(), state) as mgr:
        charm = mgr.charm
        jobs = charm.profiling_consumer.jobs()

    assert len(jobs) == 1
    assert len(jobs[0]['static_configs']) == 1

    static_config = jobs[0]['static_configs'][0]
    assert static_config['targets'] == [f'"{hostname}":{port}']
    assert static_config['labels'] == {'juju_application': remote_app_name, 'juju_charm': 'parca-k8s',
                                       'juju_model': 'baz',
                                       'juju_model_uuid': '3cdca972-b819-4f03-8d90-9c7a36d234c9',
                                       'juju_unit': f'{remote_app_name}/{remote_unit_id}'}
