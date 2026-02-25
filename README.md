# CELINE Infrastructure (`infra`)

This repository contains the **infrastructure-as-code** used to deploy and operate the CELINE platform across local, staging, and production environments.

It defines:
- Kubernetes infrastructure
- Helm / Helmfile-based deployments
- Encrypted secrets handling
- Environment-specific configuration

The repository is **operator-oriented** and assumes familiarity with Kubernetes tooling.

---

## Overview

CELINE infrastructure follows a **declarative and reproducible model** based on:

- **Helm charts** as the primary deployment unit
- **Helmfile** to coordinate multiple Helm releases
- **Helm plugins** for diffing and secrets integration
- **SOPS** for encrypted configuration
- **Task** as a convenience wrapper for common operational commands
- **Minikube** for local development

No imperative deployment scripts are used.  
Infrastructure is applied using Helmfile-driven workflows.

---

## Repository Layout

```text
infra/
├── charts/           # CELINE and third-party Helm charts
├── envs/             # Environment bindings (symlinks)
├── defaults/         # Default configurations for charts
├── helmfile.d/       # helmfile catalogue of Helm charts
└── .sops.yaml/.sops  # SOPS-encrypted secrets
```

---

## Required Tooling (Local Setup)

Local setup is **mandatory**. Install the following tools:

- `task`  
  https://taskfile.dev/docs/installation

- `minikube`  
  https://minikube.sigs.k8s.io/docs/start

- `kubectl`  
  https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/

- `helm`  
  ```bash
  curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-4 | bash
  ```

- `helm-diff` (required by Helmfile)  
  ```bash
  helm plugin install https://github.com/databus23/helm-diff --verify=false
  ```

- `helm-secrets` (required by Helmfile)  
  ```bash
    helm plugin install https://github.com/jkroepke/helm-secrets/releases/download/v4.7.5/secrets-4.7.5.tgz  --verify=false
    helm plugin install https://github.com/jkroepke/helm-secrets/releases/download/v4.7.5/secrets-getter-4.7.5.tgz  --verify=false
    helm plugin install https://github.com/jkroepke/helm-secrets/releases/download/v4.7.5/secrets-post-renderer-4.7.5.tgz  --verify=false
  ```

- `helmfile`  
  https://helmfile.readthedocs.io/en/latest/#installation

- `skaffold`  
  https://skaffold.dev/docs/install/#standalone-binary

Missing any of the above will result in a broken setup.

---

## Local Kubernetes Environment (Minikube)

Start Minikube with sufficient resources:

```bash
minikube start --cpus=4 --memory=8192
```

Ensure your kube context is set correctly:

```bash
kubectl config use-context minikube
```

---

## Local DNS Configuration (`*.celine.local`)

CELINE services rely on **Ingress host-based routing**.

For local development, services are exposed under `*.celine.local`.

Add the following entry to `/etc/hosts`:

```text
192.168.49.2 dashboard.celine.local s3.celine.local keycloak.celine.local marquez.celine.local mqtt.celine.local sso.celine.local prefect.celine.local superset.celine.local
```

Notes:
- Replace `192.168.49.2` with the output of `minikube ip` if different
- Hostnames must match ingress definitions
- OAuth redirect URIs depend on these domains

Using `localhost` will not work.

---

## Secrets Management (SOPS)

All secrets are stored **encrypted at rest**.

Typical workflows:

```bash
sops -e secrets.yaml > secrets.enc.yaml
sops -d secrets.enc.yaml
```

Helmfile integrates with `helm-secrets` to decrypt secrets at deploy time.

Plaintext secrets must never be committed.

---

## Applying Infrastructure (Helmfile)

From the `infra/` directory:

### Apply an environment

```bash
helmfile -e dev apply
```

### Diff changes before applying

```bash
helmfile -e dev diff
```

### Destroy an environment

```bash
helmfile -e dev destroy
```

### Apply a single release

```bash
helmfile -e dev apply --selector name=<release-name>
```

---

## Operational Guidelines

- Do not commit plaintext secrets
- Encrypt secrets before apply
- Prefer `helmfile diff` before `apply`
- Avoid manual `helm install`
- Keep environment changes isolated
- Production environments require additional safeguards

---

## Intended Audience

This repository is intended for:
- Infrastructure engineers
- Platform operators
- CI/CD automation

It is not intended as a general developer quickstart.

---

## Related Projects

- CELINE pipelines: https://github.com/celine-eu/celine-pipelines
- CELINE project: https://celineproject.eu/
- CELINE docs: https://celine-eu.github.io/
