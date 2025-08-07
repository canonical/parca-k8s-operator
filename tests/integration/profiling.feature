Feature: profiling

  Scenario: pull
    Given A remote charm exposing a pprof endpoint
    When The remote charm integrates to parca-k8s over profiling
    Then We can see the remote charm's workload profiles in the parca UI

  Scenario: push
    Given A remote parca-agent charm exposing a parca_store endpoint
    When The remote charm integrates to parca-k8s over parca_store
    Then We can see the remote charm's workload profiles in the parca UI
