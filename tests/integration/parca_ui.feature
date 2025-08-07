Feature: parca_ui

  Scenario: direct
    Given A parca-k8s deployment
    When The admin opens a browser at parca-application-ip:7994
    Then The admin sees the Parca UI

  Scenario: ingressed
    Given A parca-k8s deployment
    * parca-k8s is related to an ingress
    When The admin opens a browser at ingress-ip:7994
    Then The admin sees the Parca UI
