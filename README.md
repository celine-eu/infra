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

`192.168.49.2 dashboard.celine.local s3.celine.local keycloak.celine.local mqtt.celine.local sso.celine.local prefect.celine.local superset.celine.local s3.celine.local`


**Running**

Start local environment (minikube with docker driver)

`task start`

Setup infrastructure

`task apply:dev`

## Deployment flow

See [helmfile.d/](./helmfile.d)


## Configuration

### Adding secrets

Create a new env eg. staging

`task sops:create-env -- staging`

This will update `.sops.yaml` and add the AGE keys to `.sops/[env]/key.txt`. Backup the keys to ensure decryption keeps working.

### Encryption and decryption

- `task sops:encrypt` to encrypt `.envs/*/secrets.yaml` to `.envs/*/secrets.sops.yaml` 
- `task sops:decrypt` to decrypt `.envs/*/secrets.sops.yaml` to `.envs/*/secrets.yaml`

**Note** Do not version `secrets.yaml` files to avoid credential leaking