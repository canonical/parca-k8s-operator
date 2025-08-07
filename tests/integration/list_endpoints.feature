Feature: list_endpoints_action

  Scenario: direct
    Given parca-k8s deployment
    When The admin runs the `list-endpoints` juju action
    Then The admin obtains the direct parca http and grpc server urls

  Scenario: ingressed
    Given parca-k8s deployment
    * an ingress integration
    When The admin runs the `list-endpoints` juju action
    Then The admin obtains the direct parca http and grpc server urls
    * The admin obtains the ingressed parca http and grpc server urls
