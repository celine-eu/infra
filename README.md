# Deployment

Kubernetes based deployment files

## Development

**Prerequisites**

Install locally those tools:

- `task` https://taskfile.dev/docs/installation
- `minikube` https://minikube.sigs.k8s.io/docs/start
- `kubectl` https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/
- `helm` https://helm.sh/docs/intro/install/
- `helm-diff` needed by helmfile, eg `helm plugin install https://github.com/databus23/helm-diff`
- `helmfile` https://helmfile.readthedocs.io/en/latest/#installation
- `skaffold` https://skaffold.dev/docs/install/#standalone-binary

Add to `/etc/hosts`

`192.168.49.2 dashboard.celine.local s3.celine.local keycloak.celine.local mqtt.celine.local sso.celine.local prefect.celine.local superset.celine.local`


**Running**

Start local environment (minikube with docker driver)

`task dev:start`

Setup infrastructure

`task dev:setup`

## Deployment flow

See [helmfile.d/](./helmfile.d)

