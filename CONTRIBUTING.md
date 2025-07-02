# Contributing

![GitHub License](https://img.shields.io/github/license/canonical/parca-k8s-operator)
![GitHub Commit Activity](https://img.shields.io/github/commit-activity/y/canonical/parca-k8s-operator)
![GitHub Lines of Code](https://img.shields.io/tokei/lines/github/canonical/parca-k8s-operator)
![GitHub Issues](https://img.shields.io/github/issues/canonical/parca-k8s-operator)
![GitHub PRs](https://img.shields.io/github/issues-pr/canonical/parca-k8s-operator)
![GitHub Contributors](https://img.shields.io/github/contributors/canonical/parca-k8s-operator)
![GitHub Watchers](https://img.shields.io/github/watchers/canonical/parca-k8s-operator?style=social)

This documents explains the processes and practices recommended for contributing enhancements to this operator.

- Generally, before developing enhancements to this charm, you should consider [opening an issue](https://github.com/canonical/parca-k8s-operator/issues) explaining your use case.
- If you would like to chat with us about your use-cases or proposed implementation, you can reach us at [Canonical Observability Stack Matrix public channel](https://matrix.to/#/#cos:ubuntu.com) or [Discourse](https://discourse.charmhub.io/).
- Familiarising yourself with the [Charmed Operator Framework](https://juju.is/docs/sdk) library will help you a lot when working on new features or bug fixes.
- All enhancements require review before being merged. Code review typically examines:
  - code quality
  - test robustness
  - user experience for Juju administrators this charm
- When evaluating design decisions, we optimize for the following personas, in descending order of priority:
  - the Juju administrator
  - charm authors that need to integrate with this charm through relations
  - the contributors to this charm's codebase
- Please help us out in ensuring easy to review branches by rebasing your pull request branch onto the `main` branch. This also avoids merge commits and creates a linear Git commit history.

## Notable design decisions

**Limitations:** 
This charm deploys and operates a single instance of parca. Since the ingress only exposes the leader unit, scaling parca-k8s up to more than 1 unit is not supported. Replicas will effectively be unreachable and won't be able to scrape any target or collect any profiles. 


## Developing

You can use the environments created by `tox` for development:

```shell
tox --notest -e unit
source .tox/unit/bin/activate
```

### Testing

```shell
tox -e fmt           # update your code according to linting rules
tox -e lint          # code style
tox -e unit          # unit tests
tox -e integration   # integration tests
tox                  # runs 'lint' and 'unit' environments
```

### Setup

These instructions assume you will run the charm on [`microk8s`](https://microk8s.io), and relies on the `dns`, `storage`, `registry` and `metallb` plugins:

```sh
sudo snap install microk8s --classic
microk8s enable storage dns
microk8s enable metallb 192.168.0.10-192.168.0.100  # You will likely want to change these IP ranges
```

The `storage` and `dns` plugins are required machinery for most Juju charms running on K8s.
This charm is no different.
The `metallb` plugin is needed so that the Traefik ingress will receive on its service, which is of type `LoadBalancer`, an external IP it can propagate to the proxied applications.

The setup for Juju consists as follows:

```sh
sudo snap install juju --classic
juju bootstrap microk8s development
```

### Build

Build the charm in this git repository using:

```shell
charmcraft pack
```

### Container image

We are using [this rock](https://github.com/canonical/parca-rock): `ghcr.io/canonical/parca:dev`.

### Deploy

```sh
# Create a model
juju add-model parca-dev
# Enable DEBUG logging
juju model-config logging-config="<root>=INFO;unit=DEBUG"
juju deploy ./parca-k8s_ubuntu@24.04-amd64.charm  \
  --resource parca-image=ghcr.io/canonical/parca:dev \
  --resource nginx-image=ubuntu/nginx:1.24-24.04_beta \
  --resource nginx-prometheus-exporter-image=nginx/nginx-prometheus-exporter:1.1.0  \
  parca
```
