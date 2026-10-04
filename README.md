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

## Onboarding

The REC onboarding service runs as two releases on one host, `onboarding.<domain>`:
`onboarding` (the API, `charts/celine-onboarding`) and `frontend-onboarding` (the UI,
`charts/celine-frontend-onboarding`). **Both are off** until the environment sets
`onboarding.enabled: true`, which also adds the `onboarding` database to `postgres-db` and
`ONBOARDING_URL` to the `community` release.

The member wizard is public. Only `/api/admin` and `/api/me` go through oauth2-proxy, and
every `/api` path goes straight to the API, so the API sees the ingress controller as its
peer.

| Value | Effect |
|---|---|
| `onboarding.enabled` | installs both releases, the database and `ONBOARDING_URL` |
| `onboarding.forwarded_allow_ips` | **required**: the ingress controller's pod address range, uvicorn's `FORWARDED_ALLOW_IPS`. Rate limits, consent evidence and audit rows use the address it resolves. The chart refuses `*` |
| `onboarding.encryption_key` | **required**, secret: the Fernet key for personal data at rest. Losing it loses the data |
| `postgres_db.onboarding` | **required**, secret: the database role's password. It goes into a connection URI unescaped, so use a URL-safe value |
| `onboarding.client_secret`, `onboarding.cli_client_secret` | secret: `svc-onboarding` and `svc-onboarding-cli`, the same keys `policies-shell` syncs to the realm |
| `onboarding.image_tag`, `onboarding.ui_image_tag` | the API image tag, and the UI's (defaults to the API's) |
| `onboarding.host` | defaults to `onboarding.<domain>` |
| `onboarding.dataspace_enabled` | `false` by default. A template declaring `consent.data_sharing` also needs a connector, or the API does not start |
| `onboarding.sms_provider` | `none` by default: no phone verification. The service's own default, `log`, only prints the code to the pod log. `brevo` also needs `dpa_sms_signed` and `brevo_api_key` |
| `onboarding.smtp` | overrides the shared `smtp` block, as in [Email (SMTP)](#email-smtp). STARTTLS or plain only: a resolved block with `ssl: true` stops the render |
| `onboarding.templates.files`, `onboarding.templates.binary_files` | the community templates, `<slug>/<path>` to text or base64, mounted from a ConfigMap (1 MiB at most) |
| `onboarding.persistence` | `{enabled, size, storage_class}`: a volume for uploaded documents. Off by default, because document scanning is off |
| `onboarding.env` | further plain settings for the API, `NAME: value` |

`charts/celine-onboarding/examples/example-rec.values.yaml` is a complete example. The API
runs its Alembic migrations in an init container. After a template changes, import it:

```bash
kubectl -n celine-<env> exec deploy/onboarding -- \
  /app/.venv/bin/onboarding-cli import-templates --filter <slug>
```

The import checks each boundary id with the Digital Twin, so the Digital Twin must be up.

---

## Keycloak realm bootstrap

The realm's platform level (Organizations, sign-in settings, languages, themes, lifespans,
brute force, `smtpServer`, the realm role `platform-admin` and who holds it) is written by
`celine-policies keycloak bootstrap`, never by the realm import. It runs in the `policies-shell` pod's init containers:
plan (with a realm export first), apply, then a check that must find nothing left to change.
Only then does the shell run `keycloak sync`. `sync-orgs` and `sync-users` refuse a realm that
has not been through both, and `sync-users` refuses outright unless `ENV` is exactly `dev`: a
deployed realm gets its users from onboarding, not from a YAML.

Both reach Keycloak through its Service (`keycloak.internal_url`), never the public host, which
serves neither `/admin` nor `/realms/master` (see [Keycloak admin access](#keycloak-admin-access)).
Outside dev, bootstrap also hardens the **master** realm (brute force, a second factor for its
admins), and does it only as its own master-realm client `svc-celine-policies-bootstrap`. Its
first run creates that client with the master admin (`keycloak.username`/`password`); every
later run, and `sync`, signs in as the client, because the master admin's password grant stops
working once the admin has a second factor. The procedure (first console sign-in, secret
rotation, recovery) is in celine-policies
[`docs/deployment.md`, "The admin second factor and the master realm"](https://github.com/celine-eu/celine-policies/blob/main/docs/deployment.md).

The run's export, plan, apply and check output is stored under
`<bucket>/<environment>/<UTC time>-<policies_shell.image_tag>/`. Pin `policies_shell.image_tag`
to a release: the tag is the version of `platform.yaml`.

| Value | Effect |
|---|---|
| `policies_shell.bootstrap.bucket`, `policies_shell.bootstrap.s3_secret` | where the run is stored; a Secret with `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL`. Unset: logs only |
| `policies_shell.bootstrap.allow_destructive` | passes `--allow-destructive`, for a plan that turns a setting off or removes a list entry. Set for one release only |
| `policies_shell.env` | `CELINE_KEYCLOAK_ENV` for bootstrap and sync. Unset: `dev` in the helmfile environment `dev`, `prod` in any other. Anything but `dev` is production to celine-policies |
| `policies_shell.bootstrap.client_secret` | `CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_SECRET`, through a Secret, to the bootstrap init container and the shell. **Required outside dev**, at least 32 characters (`openssl rand -hex 32`): the render stops without it. The client holds master's `admin` role, so treat the secret as the master credential. Rotate it in the console first (master > Clients > `svc-celine-policies-bootstrap` > Credentials), then here |
| `policies_shell.bootstrap.client_id` | `CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_ID`. Unset: `svc-celine-policies-bootstrap` |
| `keycloak.internal_url` | the Keycloak Service bootstrap, sync and provisioning use. Default `http://keycloak-keycloakx-http` (same namespace) |
| `keycloak.brute_force.enabled` | `CELINE_KEYCLOAK_BRUTE_FORCE_ENABLED`, only when set. Unset: brute-force protection is **on** outside dev, on the platform realm and on master; `false` turns it off on both. The realm import reads `keycloak.brute_force.failureFactor` (max login failures, default 5); there is no `maxLoginFailures` in Keycloak, and an import naming one stops Keycloak from starting |
| `keycloak.admin_mfa` | `CELINE_KEYCLOAK_ADMIN_MFA_REQUIRED`, only when set: `true`/`false`, or `{enabled: true\|false}`; anything else stops the render. Unset: **on** outside dev. bootstrap owns the flow (the realm import has none): holders of the realm role `platform-admin`, and master's admins, who sign in with a password need TOTP or a recovery code, and one with neither enrols both; a passkey sign-in needs nothing more. Turning it off on a realm that has it needs `allow_destructive`. `task test:keycloak-access` checks the render |
| `keycloak.platform` | an overlay on `platform.yaml`: `realm_settings.supportedLocales`, and `platform_admin.users`, the usernames that hold the realm role `platform-admin` besides the operator realm admin |
| the resolved `smtp` block (see [Email (SMTP)](#email-smtp)) | `CELINE_KEYCLOAK_SMTP_*`; user and password through a Secret |
| `auth_setup.realmAdminUser`, `realmAdminEmail`, `realmAdminPassword` | `CELINE_KEYCLOAK_REALM_ADMIN_*`: the operator realm admin, created once (password through a Secret) and granted the realm role `platform-admin` |
| `auth_setup.clientSecret`, `domain` | `OAUTH2_PROXY_CLIENT_SECRET`, `CELINE_DOMAIN`: `sync` owns the `oauth2_proxy` client, its secret and its redirect URIs (`sso`, `superset`, `webapp`, `assistant` on the domain) |

**Who is a platform admin.** Exactly two levels (celine-policies ADR-0012). The realm role
`platform-admin`, in `realm_access.roles`, is the only platform-wide grant. An organisation's own
groups (`admins` > `managers` > `editors` > `viewers`) reach a token only inside
`organization.<alias>.groups` and count only in that organisation. There are no realm groups and
no realm roles `admin`/`manager`/`editor`/`viewer`, and no top-level `groups` claim. The Prefect
and Marquez ingresses admit `allowed_groups=role:platform-admin` only (oauth2-proxy's
`keycloak-oidc` provider names realm roles `role:<name>`). The MQTT broker has no superuser
(`goAuth.disableSuperuser`).

`task keycloak:bootstrap:check:<env>` reports drift (exit 1). Do not run `bootstrap` by hand while
the pod is starting. Rolling back is manual: put the platform-level keys back from the stored
`export.json`, then pin the previous image tag so the next start does not re-apply.

---

## Keycloak admin access

The public host `keycloak.<domain>` serves the platform realm and nothing else. Its Ingress
lists two paths, `/realms/<keycloak.realm>` and `/resources`, so sign-in, tokens, discovery,
JWKS, logout and the account console work, and everything else gets the ingress controller's
404: `/admin` (the admin console **and** the admin REST API), `/realms/master`, any other realm,
`/`. These are plain path rules: no snippet annotation, which ingress-nginx refuses by
default. A gateway firewall or WAF in front does not change this.

| Value | Effect |
|---|---|
| `keycloak.public_realms` | the realms the public host serves. Default: `[<keycloak.realm>]`. `master` stops the render |
| `keycloak.admin_forward_port` | the localhost port of the admin console, `KC_HOSTNAME_ADMIN=http://localhost:<port>`. Default `18080` |

**There is no admin ingress.** The master realm and the admin console are reached only through
`kubectl port-forward` to the Keycloak Service, from a machine with cluster credentials:

```bash
task keycloak:forward                       # current kubectl context and namespace
task keycloak:forward NAMESPACE=celine-dev  # or name the namespace
# -> Admin console: http://localhost:18080/admin/master/console/   (Ctrl-C to stop)
```

The task reads the port from the deployed `KC_HOSTNAME_ADMIN`; the console works only on that
exact URL. localhost is a secure context, so plain http needs no certificate. `KC_HOSTNAME`
stays the public URL: every realm's issuer, and every token minted inside the cluster, is
unchanged.

**One-time, per Keycloak: master's Frontend URL.** The console signs in to master through
master's own URL, and `KC_HOSTNAME_ADMIN` does not change that (measured on Keycloak 26.6.0 and
26.7.3: without it the console calls the public host for master and never shows a login). So
master's Frontend URL must be the same localhost URL. `task keycloak:forward` warns while it is
not. With the forward running, as the bootstrap client (its secret is
`policies_shell.bootstrap.client_secret`; `read -rs` keeps it out of the shell history):

```bash
U=http://localhost:18080
read -rs S
T=$(curl -s -d grant_type=client_credentials -d client_id=svc-celine-policies-bootstrap \
      --data-urlencode client_secret="$S" "$U/realms/master/protocol/openid-connect/token" \
    | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
curl -s -X PUT -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d "{\"attributes\":{\"frontendUrl\":\"$U\"}}" "$U/admin/realms/master"
unset S T
```

Master's tokens then carry the issuer `http://localhost:18080/realms/master`; celine-policies
checks only that it ends in `/realms/master`. The first console sign-in of a master admin outside
dev enrols TOTP and recovery codes.

**Inside the cluster** nothing administers Keycloak through the public host: the policies-shell
bootstrap and sync, and provisioning, use `keycloak.internal_url`. Services keep validating
tokens against the public realm URL, which the public host still serves.

`task test:keycloak-access` renders all of it. On a throwaway minikube with ingress-nginx
v1.14.3, Keycloak 26.7.3 and the rendered Ingress (2026-10-04):
- the public host answered 404 to `/admin/*`, `/realms/master/*`, `/` and other realms,
  `..` and `%2f` variants included;
- sign-in and the account console of the platform realm worked;
- the console signed in through `task keycloak:forward`;
- bootstrap's calls through the Service worked.

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
- `celine-onboarding` — REC onboarding API (member wizard and operator console). See [Onboarding](#onboarding)
- `celine-grid` — Grid resilience API

### Frontends

- `celine-frontend-assistant` — AI Assistant webapp
- `celine-frontend-roi` — ROI webapp
- `celine-frontend-webapp` — Participant webapp
- `celine-frontend-grid` — Grid webapp
- `celine-frontend-onboarding` — REC onboarding UI, and the host the onboarding API answers on

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
