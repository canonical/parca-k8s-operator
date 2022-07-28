# Parca Kubernetes Operator

Parca provides continuous profiling for analysis of CPU and memory usage, down to the line number
and throughout time. Saving infrastructure cost, improving performance, and increasing reliability.

This operator builds a simple deployment of the Parca server and provides a relation interface such
that it can be integrated with other Juju charms in a model.

## Usage

You can deploy the operator as such:

```shell
# Deploy the charm
$ juju deploy parca-k8s --trust --channel edge
```

Once the deployment is complete, grab the address of the Parca application:

```bash
$ juju show-unit parca/0 --format=json | jq -r '.["parca-k8s/0"]["public-address"]'
```

Now visit: `http://<parca-address>:7070/` to see the Parca dashboard.

## Configuration

By default, Parca will store profiles **in memory**. This is the current default, as the
persistence settings are very new and prone to breaking! The default limit for in-memory storage is
4096MB. When Parca reaches that limit, profiles are purged.

The in-memory storage limit is configurable like so:

```bash
# Increase limit to 8192MB
$ juju config parca-k8s memory-storage-limit=8192
```

If you wish to enable the **experimental** storage persistence, you can do as such:

```bash
$ juju config parca-k8s storage-persist=true
```
