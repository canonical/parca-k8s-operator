Feature: ingress
  Scenario Outline: curl
    Given parca-k8s deployment
    * an ingress integration
    When I curl the $<port> in $<mode> mode
    Then I get a 200 code
    Examples:
        | port | mode      |
        | 7994 | direct    |
        | 7993 | ingressed |
