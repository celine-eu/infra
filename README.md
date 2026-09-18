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

## Local DNS Configuration (`*.celine.test`)

CELINE services rely on **Ingress host-based routing**.

For local development, services are exposed under `*.celine.test` where `*.test` is resolved by minikube

Add the following entry to `/etc/hosts`:

```text
192.168.49.2 api.celine.test webapp.celine.test assistant.celine.test dashboard.celine.test s3.celine.test keycloak.celine.test marquez.celine.test mqtt.celine.test sso.celine.test prefect.celine.test superset.celine.test
```

Notes:
- Replace `192.168.49.2` with the output of `minikube ip` if different
- Hostnames must match ingress definitions
- OAuth redirect URIs depend on these domains

Plain `localhost` will not work due to OIDC issuer mismatch.

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

## Email (SMTP)

Services that send email read one shared `smtp` block from the environment's values and
secrets:

```yaml
smtp:
  host: ""            # empty: no email
  port: 587
  ssl: false
  starttls: true
  auth: true          # defaults to true when `user` is set
  user: ""            # keep in secrets
  password: ""        # keep in secrets
  from: ""
  fromDisplayName: ""
  replyTo: ""
```

A service can use a different provider through its own block, `keycloak.smtp` or
`superset.smtp`:

- **Provider settings** (`host`, `port`, `ssl`, `starttls`, `auth`, `user`, `password`)
  are taken as a whole. The service's own block is used when its `host` is set.
  Otherwise the shared block is used. Values are never mixed across blocks.
- **Sender settings** fall back one at a time. Each field comes from the service's own
  block when set there, and otherwise from the shared block. The Keycloak fields are
  `from`, `fromDisplayName` and `replyTo`. Superset's is `superset.smtp.mailFrom`, which
  falls back to `smtp.from`.
- Keycloak email is off when `keycloak.smtp.enabled` is `false`, even if a host resolves.
- Superset's image always uses STARTTLS, so it ignores `ssl` and `starttls`.

The Keycloak realm import applies SMTP to a **new** realm only. An existing realm is not
changed by the import.

The provisioning service does not send email itself: it asks Keycloak to. Its
`provisioning.email_mode` defaults to `dev`, which emails nobody except the addresses in
`provisioning.email_dev_recipients`. Set `deliver` only in an environment that should
email participants.

---

## Keycloak realm bootstrap

The realm's platform level (Organizations, sign-in settings, languages, themes, lifespans,
brute force, `smtpServer`, the realm role groups) is written by `celine-policies keycloak
bootstrap`, never by the realm import. It runs in the `policies-shell` pod's init containers:
plan (with a realm export first), apply, then a check that must find nothing left to change.
Only then does the shell run `keycloak sync`. `sync-orgs` and `sync-users` refuse a realm that
has not been through both.

The run's export, plan, apply and check output is stored under
`<bucket>/<environment>/<UTC time>-<policies_shell.image_tag>/`. Pin `policies_shell.image_tag`
to a release: the tag is the version of `platform.yaml`.

| Value | Effect |
|---|---|
| `policies_shell.bootstrap.bucket`, `policies_shell.bootstrap.s3_secret` | where the run is stored; a Secret with `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL`. Unset: logs only |
| `policies_shell.bootstrap.allow_destructive` | passes `--allow-destructive`, for a plan that turns a setting off or removes a list entry. Set for one release only |
| `keycloak.brute_force.enabled` | `CELINE_KEYCLOAK_BRUTE_FORCE_ENABLED`, only when set. Unset: brute-force protection is **on**. The realm import reads `keycloak.brute_force.failureFactor` (max login failures, default 5); there is no `maxLoginFailures` in Keycloak, and an import naming one stops Keycloak from starting |
| `keycloak.admin_mfa` | `true`/`false`, or `{enabled: true\|false}`; anything else stops the render. The realm import only (new realms): members of `/admins` (realm role `admin`) who sign in with a password need TOTP or a recovery code, and one with neither enrols both; a passkey sign-in needs nothing more. `task test:auth-setup` checks the render |
| `keycloak.platform` | an overlay on `platform.yaml`: `realm_settings.supportedLocales` only |
| the resolved `smtp` block (see [Email (SMTP)](#email-smtp)) | `CELINE_KEYCLOAK_SMTP_*`; user and password through a Secret |
| `auth_setup.realmAdminUser`, `realmAdminEmail`, `realmAdminPassword` | `CELINE_KEYCLOAK_REALM_ADMIN_*`: the operator realm admin, created once (password through a Secret) and kept in `/admins` |
| `auth_setup.clientSecret`, `domain` | `OAUTH2_PROXY_CLIENT_SECRET`, `CELINE_DOMAIN`: `sync` owns the `oauth2_proxy` client, its secret and its redirect URIs (`sso`, `superset`, `webapp`, `assistant` on the domain) |

`task keycloak:bootstrap:check:<env>` reports drift (exit 1). Do not run `bootstrap` by hand while
the pod is starting. Rolling back is manual: put the platform-level keys back from the stored
`export.json`, then pin the previous image tag so the next start does not re-apply.

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

## Local Charts

Charts under `charts/` are deployed from the working tree by `helmfile.d/`.
`defaults/` is the integration point between `envs/**/values.yaml` and each chart's
`values.yaml`, so environment variables stay simple and are reused across charts.

### Shared

- `celine-services` — library chart (`type: library`) providing the shared deployment,
  service, ingress, secret and env templates every `celine-*` service chart includes
- `api-gateway` — exposes all CELINE APIs under a single ingress, `api.domain.tld/<service>`

### Services

- `celine-dataset-api` — Dataset API
- `celine-dataset-api-shell` — Dataset API CLI to manage datasets
- `celine-digital-twin` — Digital Twin API
- `celine-flexibility-api` — Flexibility API
- `celine-mqtt-auth` — mosquitto-go-auth compatible API endpoint for MQTT auth/ACL
- `celine-nudging` — Nudging API
- `celine-policies-shell` — Policies CLI to manage Keycloak. On every start, init containers run
  `keycloak bootstrap` (the realm's platform level, from `platform.yaml` in the image) and store the
  run, then the shell runs `keycloak sync`; a failure of either fails the pod. See
  [Keycloak realm bootstrap](#keycloak-realm-bootstrap)
- `celine-rec-registry` — REC Registry API
- `celine-rec-registry-shell` — REC Registry CLI to manage REC organizations and asset metadata
- `celine-ai-assistant` — AI Assistant API
- `celine-roi` — ROI API
- `celine-webapp` — Participant webapp API
- `celine-grid` — Grid resilience API

### Frontends

- `celine-frontend-assistant` — AI Assistant webapp
- `celine-frontend-roi` — ROI webapp
- `celine-frontend-webapp` — Participant webapp
- `celine-frontend-grid` — Grid webapp

### Platform

- `auth-setup` — secrets and configmaps for `oauth2-proxy` and `keycloak`
- `mqtt-setup` — MQTT access for services
- `mqtt-ingestor` — ingests every MQTT message on the configured topics into a database
- `marquez` — Marquez OpenLineage endpoints and UI
- `mosquitto-go-auth` — mosquitto with the mosquitto-go-auth module
- `pg-freezer` — cold storage service mirroring table records to minio/s3 as parquet, then cleaning the tables
- `postgres-db` — CNPG-specific configuration for the database/user maps
- `prefect-pipelines` — collects and deploys the CELINE data pipelines
- `registry-accounts` — docker/ghcr.io secrets for image pulling
- `s3-accounts` — minio/s3 access credentials
- `tls-setup` — TLS for local (self-signed) and production (Let's Encrypt) environments

---

## Related Projects

- CELINE pipelines: https://github.com/celine-eu/celine-pipelines
- CELINE project: https://celineproject.eu/
- CELINE docs: https://celine-eu.github.io/
