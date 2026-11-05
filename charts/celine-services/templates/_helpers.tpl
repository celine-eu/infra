{{/*
==============================================================================
  celine-services — library helpers
  All templates prefixed "celine-services.*".
  Called from service charts via {{ include "celine-services.<n>" . }}
==============================================================================
*/}}


{{/* ----------------------------------------------------------------------------
  Naming
----------------------------------------------------------------------------- */}}

{{- define "celine-services.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "celine-services.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/* Secret name — always <fullname>-secrets */}}
{{- define "celine-services.secretName" -}}
{{- printf "%s-secrets" (include "celine-services.fullname" .) }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  Labels
----------------------------------------------------------------------------- */}}

{{- define "celine-services.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "celine-services.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default "latest" | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* ----------------------------------------------------------------------------
  Image reference — `repository:tag`, or `repository:tag@sha256:…` when
  .Values.imageDigests (the environment's `image_digests`) lists a digest for that
  repository and tag. Keyed by tag, so a tag bumped without its digest renders unpinned,
  never the old image under the new tag. A listed value that is not a sha256 digest
  stops the render.
    {{ include "celine-services.image" . }}                         — .Values.image
    {{ include "celine-services.imageRef" (dict "repository" r "tag" t "digests" m) }}
----------------------------------------------------------------------------- */}}

{{- define "celine-services.image" -}}
{{- include "celine-services.imageRef" (dict "repository" .Values.image.repository "tag" (.Values.image.tag | default "latest") "digests" .Values.imageDigests) -}}
{{- end }}

{{- define "celine-services.imageRef" -}}
{{- $tag := .tag | toString -}}
{{- $digest := "" -}}
{{- with .digests -}}
{{- with get . $.repository -}}
{{- $digest = get . $tag | toString -}}
{{- end -}}
{{- end -}}
{{- if $digest -}}
{{- if not (regexMatch "^sha256:[a-f0-9]{64}$" $digest) -}}
{{- fail (printf "image_digests.%s.%s must be a sha256 digest (sha256:<64 hex>), got %q" .repository $tag $digest) -}}
{{- end -}}
{{- printf "%s:%s@%s" .repository $tag $digest -}}
{{- else -}}
{{- printf "%s:%s" .repository $tag -}}
{{- end -}}
{{- end }}

{{- define "celine-services.selectorLabels" -}}
app.kubernetes.io/name: {{ include "celine-services.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  Posture env var — CELINE_ENV (celine-sdk `posture`)
  Only the value `dev` relaxes a service; unset or anything else is hardened.
  Rendered when .Values.celineEnv is set (the helmfile defaults set it per
  environment, and refuse `dev` outside the dev environment).
----------------------------------------------------------------------------- */}}

{{- define "celine-services.postureEnv" -}}
{{- with .Values.celineEnv -}}
- name: CELINE_ENV
  value: {{ . | quote }}
{{- end }}
{{- end }}

{{/* ----------------------------------------------------------------------------
  Access-audit pseudonym key — CELINE_AUDIT_PSEUDONYM_KEY (celine-sdk `audit`)
  The HMAC key `pseudonymise` uses for person ids in the access audit; without
  it the SDK falls back to unkeyed SHA-256. One key per environment, so a
  person's pseudonym is the same in every service. From the release's Secret
  (celine-services.secret), only when .Values.audit.pseudonymKey is set.
----------------------------------------------------------------------------- */}}

{{- define "celine-services.auditEnv" -}}
{{- if (.Values.audit | default dict).pseudonymKey -}}
- name: CELINE_AUDIT_PSEUDONYM_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "celine-services.secretName" . }}
      key: CELINE_AUDIT_PSEUDONYM_KEY
{{- end }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  OIDC env vars — CELINE_OIDC_* (celine-sdk OidcSettings)
  Global defaults come from .Values.oidc.*
  Per-service overrides (clientId, clientSecret, audience) from
  .Values.oidc.service.* — these default to svc-<release-name>.
----------------------------------------------------------------------------- */}}

{{- define "celine-services.oidcEnv" -}}
{{- $secretName := include "celine-services.secretName" . }}
{{- $defaultSvcId := printf "svc-%s" .Release.Name }}
- name: CELINE_OIDC_BASE_URL
  value: {{ .Values.oidc.baseUrl | quote }}
- name: CELINE_OIDC_JWKS_URI
  value: {{ .Values.oidc.jwksUri | quote }}

{{- if not (.Values.oidc.service | dig "passthrough" false) }}
- name: CELINE_OIDC_CLIENT_ID
  value: {{ .Values.oidc.service.clientId | default $defaultSvcId | quote }}
- name: CELINE_OIDC_AUDIENCE
  value: {{ .Values.oidc.service.audience | default $defaultSvcId | quote }}
- name: CELINE_OIDC_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: CELINE_OIDC_CLIENT_SECRET
- name: CELINE_OIDC_INCLUDE_CLIENT_ID_AS_AUDIENCE
  value: {{ .Values.oidc.includeClientIdAsAudience | toString | quote }}
{{- else }}
- name: CELINE_OIDC_AUDIENCE
  value: {{ .Values.oidc.service | dig "audience" "oauth2_proxy" | quote }}
- name: CELINE_OIDC_INCLUDE_CLIENT_ID_AS_AUDIENCE
  value: "false"
{{- end }}

{{- if .Values.oidc.allowedAudiences }}
- name: CELINE_OIDC_ALLOWED_AUDIENCES
  value: {{ .Values.oidc.allowedAudiences | quote }}
{{- end }}

- name: CELINE_OIDC_TIMEOUT
  value: {{ .Values.oidc.timeout | toString | quote }}
{{- end }}



{{/* ----------------------------------------------------------------------------
  Policies env vars — CELINE_POLICIES_*
  Global defaults from .Values.policies.* — all services share the same
  policy engine settings unless overridden locally.
----------------------------------------------------------------------------- */}}

{{- define "celine-services.policiesEnv" -}}
- name: CELINE_POLICIES_DIR
  value: {{ .Values.policies.dir | default "/app/policies" | quote }}
{{- if .Values.policies.dataDir }}
- name: CELINE_POLICIES_DATA_DIR
  value: {{ .Values.policies.dataDir | quote }}
{{- end }}
- name: CELINE_POLICIES_CACHE_ENABLED
  value: {{ .Values.policies.cacheEnabled | default true | toString | quote }}
- name: CELINE_POLICIES_CACHE_TTL
  value: {{ .Values.policies.cacheTtl | default 300 | toString | quote }}
- name: CELINE_POLICIES_CACHE_MAXSIZE
  value: {{ .Values.policies.cacheMaxsize | default 10000 | toString | quote }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  Postgres env — DSN from existing Secret produced by charts/postgres-db
----------------------------------------------------------------------------- */}}

{{- define "celine-services.postgresEnv" -}}
{{- if .Values.postgres.existingSecret }}
- name: {{ .Values.postgres.dsnEnvVar | default "DATABASE_URL" }}
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgres.existingSecret }}
      key: {{ .Values.postgres.secretKey | default "connection-string" }}
{{- end }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  S3 env — endpoint + credentials from existing Secret (charts/s3-accounts)
----------------------------------------------------------------------------- */}}

{{- define "celine-services.s3Env" -}}
{{- if .Values.s3.endpointUrl }}
- name: S3_ENDPOINT_URL
  value: {{ .Values.s3.endpointUrl | quote }}
{{- end }}
- name: S3_REGION
  value: {{ .Values.s3.region | default "us-east-1" | quote }}
{{- if .Values.s3.existingSecret }}
- name: S3_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ .Values.s3.existingSecret }}
      key: AWS_ACCESS_KEY_ID
- name: S3_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.s3.existingSecret }}
      key: AWS_SECRET_ACCESS_KEY
{{- end }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  CA env + volume — injects SSL_CERT_FILE and REQUESTS_CA_BUNDLE pointing at
  the custom CA cert when .Values.caSecret is set.
  Covers all httpx calls (OIDC, DT, nudging APIs) and the ssl module (aiomqtt)
  without any SDK code changes.
----------------------------------------------------------------------------- */}}

{{- define "celine-services.caEnv" -}}
{{- if .Values.caSecret }}
- name: SSL_CERT_FILE
  value: /etc/ssl/celine-ca/ca.crt
- name: REQUESTS_CA_BUNDLE
  value: /etc/ssl/celine-ca/ca.crt
{{- end }}
{{- end }}

{{- define "celine-services.caVolumeMount" -}}
{{- if .Values.caSecret }}
- name: celine-ca
  mountPath: /etc/ssl/celine-ca
  readOnly: true
{{- end }}
{{- end }}

{{- define "celine-services.caVolume" -}}
{{- if .Values.caSecret }}
- name: celine-ca
  secret:
    secretName: {{ .Values.caSecret }}
    items:
      - key: ca.crt
        path: ca.crt
{{- end }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  Alembic migration initContainer
  Included only when .Values.migrate.enabled is true.
  Inherits the postgres env so alembic can reach the DB.
----------------------------------------------------------------------------- */}}

{{- define "celine-services.migrateInitContainer" -}}
{{- if .Values.migrate.enabled }}
initContainers:
  - name: migrate
    image: {{ include "celine-services.image" . | quote }}
    imagePullPolicy: {{ .Values.image.pullPolicy | default "IfNotPresent" }}
    command: {{ .Values.migrate.command | default (list "alembic" "upgrade" "head") | toJson }}
    securityContext:
      allowPrivilegeEscalation: false
      capabilities:
        drop: [ALL]
    {{- /* `migrate.resources`: unset, the namespace's LimitRange decides (if any). */}}
    {{- with (.Values.migrate | default dict).resources }}
    resources:
      {{- toYaml . | nindent 6 }}
    {{- end }}
    env:
      {{- include "celine-services.postureEnv" . | nindent 6 }}
      {{- include "celine-services.postgresEnv" . | nindent 6 }}
      {{- include "celine-services.caEnv" . | nindent 6 }}
      {{- with .Values.migrate.extraEnv }}
      {{- toYaml . | nindent 6 }}
      {{- end }}
    {{- if .Values.caSecret }}
    volumeMounts:
      {{- include "celine-services.caVolumeMount" . | nindent 6 }}
    {{- end }}
{{- end }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  ConfigMap volume + mount
  Used by services that need a config directory (e.g. celine-digital-twin).
----------------------------------------------------------------------------- */}}

{{- define "celine-services.configVolume" -}}
- name: config
  configMap:
    name: {{ .Values.configMap.name }}
{{- end }}

{{- define "celine-services.configVolumeMount" -}}
- name: config
  mountPath: {{ .Values.configMap.mountPath | default "/app/config" }}
  readOnly: true
{{- end }}


{{/* ----------------------------------------------------------------------------
  Secret
  Renders a K8s Secret from values. Secrets are passed in plaintext at
  deploy time via helmfile + helm-secrets (SOPS). The chart owns the Secret
  so it is created/updated on every helm upgrade.
----------------------------------------------------------------------------- */}}

{{- define "celine-services.secret" -}}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "celine-services.secretName" . }}
  labels:
    {{- include "celine-services.labels" . | nindent 4 }}
type: Opaque
stringData:
  {{- if not (.Values.oidc.service | dig "passthrough" false) }}
  CELINE_OIDC_CLIENT_SECRET: {{ .Values.oidc.service.clientSecret | quote }}
  {{- end }}
  {{- with (.Values.audit | default dict).pseudonymKey }}
  CELINE_AUDIT_PSEUDONYM_KEY: {{ . | quote }}
  {{- end }}
  {{- with .Values.extraSecrets }}
  {{- toYaml . | nindent 2 }}
  {{- end }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  Service
----------------------------------------------------------------------------- */}}

{{- define "celine-services.service" -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "celine-services.fullname" . }}
  labels:
    {{- include "celine-services.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type | default "ClusterIP" }}
  selector:
    {{- include "celine-services.selectorLabels" . | nindent 4 }}
  ports:
    - name: http
      port: {{ .Values.service.port }}
      targetPort: http
      protocol: TCP
{{- end }}


{{/* ----------------------------------------------------------------------------
  Ingress — with oauth2-proxy annotations
----------------------------------------------------------------------------- */}}

{{- define "celine-services.ingress" -}}
{{- if .Values.ingress.enabled }}
{{- $traefik := include "celine-services.traefik.on" . }}
{{- $fullname := include "celine-services.fullname" . }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "celine-services.fullname" . }}
  labels:
    {{- include "celine-services.labels" . | nindent 4 }}
  annotations:
    {{- if $traefik }}
    {{- include "celine-services.traefik.annotations" (dict "root" . "middlewares" (list (ternary (include "celine-services.traefik.authMiddleware" .) "" (.Values.ingress.auth.enabled | default false)) (printf "%s-rewrite" $fullname))) | nindent 4 }}
    {{- with include "celine-services.traefik.extraAnnotations" (dict "root" . "annotations" .Values.ingress.annotations) }}{{ . | nindent 4 }}{{ end }}
    {{- else }}
    cert-manager.io/issuer: {{ .Values.ingress.issuer | default "celine-tls" }}
    nginx.ingress.kubernetes.io/rewrite-target: /
    {{- if .Values.ingress.auth.enabled }}
    nginx.ingress.kubernetes.io/auth-url: "https://sso.{{ .Values.ingress.domain }}/oauth2/auth"
    nginx.ingress.kubernetes.io/auth-signin: "https://sso.{{ .Values.ingress.domain }}/oauth2/start?rd=$scheme://$host$request_uri"
    nginx.ingress.kubernetes.io/auth-response-headers: "Authorization, X-Auth-Request-Email, X-Auth-Request-User, X-Auth-Request-Access-Token"
    # The auth answer carries the access token twice (Authorization and
    # X-Auth-Request-Access-Token): past nginx's default buffer it fails as "upstream sent too
    # big header" and every page answers 500.
    nginx.ingress.kubernetes.io/proxy-buffer-size: "32k"
    {{- end }}
    {{- with .Values.ingress.annotations }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
    {{- end }}
spec:
  ingressClassName: {{ $.Values.ingressClassName | default "nginx" }}
  rules:
    - host: {{ .Values.ingress.host }}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {{ include "celine-services.fullname" . }}
                port:
                  number: {{ .Values.service.port }}
  {{- if .Values.ingress.tls.enabled }}
  tls:
    - hosts:
        - {{ .Values.ingress.host }}
      secretName: {{ .Values.ingress.tls.secretName | default (printf "%s-tls" (include "celine-services.fullname" .)) }}
  {{- end }}
{{- if .Values.ingress.auth.enabled }}
---
# Step 1: clear the oauth2-proxy session, then send browser to /keycloak-logout.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "celine-services.fullname" . }}-signout
  labels:
    {{- include "celine-services.labels" . | nindent 4 }}
  annotations:
    {{- if $traefik }}
    {{- include "celine-services.traefik.annotations" (dict "root" . "middlewares" (list (printf "%s-signout" $fullname))) | nindent 4 }}
    {{- else }}
    cert-manager.io/issuer: {{ .Values.ingress.issuer | default "celine-tls" }}
    nginx.ingress.kubernetes.io/permanent-redirect: "https://sso.{{ .Values.ingress.domain }}/oauth2/sign_out?rd=https://{{ .Values.ingress.host }}/keycloak-logout"
    nginx.ingress.kubernetes.io/permanent-redirect-code: "302"
    {{- end }}
spec:
  ingressClassName: {{ $.Values.ingressClassName | default "nginx" }}
  rules:
    - host: {{ .Values.ingress.host }}
      http:
        paths:
          # Prefix, not Exact: Traefik ranks routers by rule length and writes a Prefix
          # longer, so an Exact path here lost to /oauth2 (tests/ingress-routing).
          - path: /oauth2/sign_out
            pathType: Prefix
            backend:
              service:
                name: {{ include "celine-services.fullname" . }}
                port:
                  number: {{ .Values.service.port }}
  {{- if .Values.ingress.tls.enabled }}
  tls:
    - hosts:
        - {{ .Values.ingress.host }}
      secretName: {{ .Values.ingress.tls.secretName | default (printf "%s-tls" (include "celine-services.fullname" .)) }}
  {{- end }}
---
# Step 2: send browser to Keycloak logout with client_id (no id_token_hint needed)
# and post_logout_redirect_uri back to this service.
# Keycloak validates post_logout_redirect_uri against oauth2_proxy client's
# postLogoutRedirectUris list — ensure https://{host}/* is configured there.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "celine-services.fullname" . }}-keycloak-logout
  labels:
    {{- include "celine-services.labels" . | nindent 4 }}
  annotations:
    {{- if $traefik }}
    {{- include "celine-services.traefik.annotations" (dict "root" . "middlewares" (list (printf "%s-keycloak-logout" $fullname))) | nindent 4 }}
    {{- else }}
    cert-manager.io/issuer: {{ .Values.ingress.issuer | default "celine-tls" }}
    nginx.ingress.kubernetes.io/permanent-redirect: "{{ .Values.ingress.keycloakRealmUrl | default (printf "https://keycloak.%s/realms/celine" .Values.ingress.domain) }}/protocol/openid-connect/logout?client_id=oauth2_proxy&post_logout_redirect_uri=https://{{ .Values.ingress.host }}/"
    nginx.ingress.kubernetes.io/permanent-redirect-code: "302"
    {{- end }}
spec:
  ingressClassName: {{ $.Values.ingressClassName | default "nginx" }}
  rules:
    - host: {{ .Values.ingress.host }}
      http:
        paths:
          - path: /keycloak-logout
            pathType: Exact
            backend:
              service:
                name: {{ include "celine-services.fullname" . }}
                port:
                  number: {{ .Values.service.port }}
  {{- if .Values.ingress.tls.enabled }}
  tls:
    - hosts:
        - {{ .Values.ingress.host }}
      secretName: {{ .Values.ingress.tls.secretName | default (printf "%s-tls" (include "celine-services.fullname" .)) }}
  {{- end }}
{{- end }}
{{- if $traefik }}
---
# rewrite-target: / (every path to the service's root), as the nginx mode.
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: {{ $fullname }}-rewrite
  labels:
    {{- include "celine-services.labels" . | nindent 4 }}
spec:
  replacePath:
    path: /
{{- if .Values.ingress.auth.enabled }}
---
{{ include "celine-services.traefik.redirectMiddleware" (dict "root" . "name" (printf "%s-signout" $fullname) "target" (printf "https://sso.%s/oauth2/sign_out?rd=https://%s/keycloak-logout" .Values.ingress.domain .Values.ingress.host)) }}
---
{{ include "celine-services.traefik.redirectMiddleware" (dict "root" . "name" (printf "%s-keycloak-logout" $fullname) "target" (printf "%s/protocol/openid-connect/logout?client_id=oauth2_proxy&post_logout_redirect_uri=https://%s/" (.Values.ingress.keycloakRealmUrl | default (printf "https://keycloak.%s/realms/celine" .Values.ingress.domain)) .Values.ingress.host)) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  Security headers — response headers ingress-nginx adds on the Ingress paths that
  carry the annotation (`nginx.ingress.kubernetes.io/custom-headers`, a ConfigMap in
  the release's namespace). The controller serves only header names listed in its
  `global-allowed-response-headers`; a name missing there makes it answer 503 on
  every path of that Ingress, so the controller is configured first (README,
  "Security headers").

  .Values.securityHeaders:
    enabled            false renders neither the ConfigMap nor the annotation
    referrerPolicy     default strict-origin-when-cross-origin
    frameOptions       X-Frame-Options, default DENY; "" leaves it out
    permissionsPolicy  default: no camera, microphone, geolocation, payment, usb
    hsts               Strict-Transport-Security when the Ingress terminates TLS
                       (ingress.tls.enabled); "" leaves it out
    csp.reportOnly     true sends Content-Security-Policy-Report-Only instead
    csp.sentByApp      true: the application sends its own Content-Security-Policy
                       (a SvelteKit app, with a per-request nonce in script-src). The
                       controller would replace it with an enforced policy of the
                       same name, so the ConfigMap carries none; it may still carry a
                       Report-Only policy, without script-src or default-src (the app
                       owns scripts). Anything else stops the render.
    csp.directives     directive -> sources; each value goes through `tpl`, so
                       "https://keycloak.{{ .Values.ingress.domain }}" works. true
                       renders a bare directive, "" / false / null leave it out.
    apiCsp             { reportOnly, directives }: the policy of the host's own API
                       Ingress, when it has one that the controller serves headers on
                       (`apiAnnotation`); a second ConfigMap `<name>-api-security-headers`
                       with the same other headers. Unset: the API Ingress names the
                       host's ConfigMap.
----------------------------------------------------------------------------- */}}

{{- define "celine-services.securityHeaders.name" -}}
{{- printf "%s-security-headers" (include "celine-services.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "celine-services.securityHeaders.enabled" -}}
{{- if (.Values.securityHeaders | default dict).enabled }}true{{ end }}
{{- end }}

{{/* The annotation, for an Ingress's metadata.annotations; nothing when disabled. */}}
{{- define "celine-services.securityHeaders.annotation" -}}
{{- if include "celine-services.securityHeaders.enabled" . -}}
nginx.ingress.kubernetes.io/custom-headers: {{ printf "%s/%s" .Release.Namespace (include "celine-services.securityHeaders.name" .) | quote }}
{{- end }}
{{- end }}

{{/* The annotation for the host's API Ingress: its own ConfigMap when apiCsp is set. */}}
{{- define "celine-services.securityHeaders.apiAnnotation" -}}
{{- if include "celine-services.securityHeaders.enabled" . -}}
{{- if (.Values.securityHeaders | default dict).apiCsp -}}
nginx.ingress.kubernetes.io/custom-headers: {{ printf "%s/%s" .Release.Namespace (include "celine-services.securityHeaders.apiName" .) | quote }}
{{- else -}}
{{ include "celine-services.securityHeaders.annotation" . }}
{{- end }}
{{- end }}
{{- end }}

{{- define "celine-services.securityHeaders.apiName" -}}
{{- printf "%s-api-security-headers" (include "celine-services.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* The Content-Security-Policy value of (dict "root" . "csp" <csp>): the directives, sorted, joined by "; ". */}}
{{- define "celine-services.securityHeaders.csp" -}}
{{- $root := .root }}
{{- $out := list }}
{{- range $name, $value := (.csp | default dict).directives }}
{{- if kindIs "bool" $value }}
{{- if $value }}{{ $out = append $out $name }}{{ end }}
{{- else if $value }}
{{- $out = append $out (printf "%s %s" $name (tpl (toString $value) $root | trim)) }}
{{- end }}
{{- end }}
{{- join "; " $out }}
{{- end }}

{{/* name -> value of every header, as YAML; values checked against what the controller accepts. */}}
{{- define "celine-services.securityHeaders.data" -}}
{{- include "celine-services.securityHeaders.dataWith" (dict "root" . "csp" ((.Values.securityHeaders | default dict).csp | default dict)) }}
{{- end }}

{{/* The same, for (dict "root" . "csp" <csp>): the host's headers with that policy. */}}
{{- define "celine-services.securityHeaders.dataWith" -}}
{{- $root := .root }}
{{- $csp := .csp | default dict }}
{{- $sh := $root.Values.securityHeaders | default dict }}
{{- $tls := dig "ingress" "tls" "enabled" false ($root.Values | toYaml | fromYaml) }}
{{- $headers := dict "X-Content-Type-Options" "nosniff" }}
{{- $_ := set $headers "Referrer-Policy" (hasKey $sh "referrerPolicy" | ternary $sh.referrerPolicy "strict-origin-when-cross-origin") }}
{{- $_ := set $headers "X-Frame-Options" (hasKey $sh "frameOptions" | ternary $sh.frameOptions "DENY") }}
{{- $_ := set $headers "Permissions-Policy" (hasKey $sh "permissionsPolicy" | ternary $sh.permissionsPolicy "camera=(), microphone=(), geolocation=(), payment=(), usb=()") }}
{{- if $tls }}
{{- $_ := set $headers "Strict-Transport-Security" (hasKey $sh "hsts" | ternary $sh.hsts "max-age=31536000; includeSubDomains") }}
{{- end }}
{{- with include "celine-services.securityHeaders.csp" (dict "root" $root "csp" $csp) }}
{{- if $csp.sentByApp }}
{{- if not $csp.reportOnly }}
{{- fail (printf "securityHeaders: %s sends its own Content-Security-Policy (csp.sentByApp); an enforced policy from the controller would replace it, so only a Report-Only one may be set here" (include "celine-services.fullname" $root)) }}
{{- end }}
{{- range $name := list "script-src" "default-src" }}
{{- if hasKey ($csp.directives | default dict) $name }}{{ if index $csp.directives $name }}
{{- fail (printf "securityHeaders: %s sends its own Content-Security-Policy (csp.sentByApp) and owns its scripts; its Report-Only policy may not set %s" (include "celine-services.fullname" $root) $name) }}
{{- end }}{{ end }}
{{- end }}
{{- end }}
{{- $_ := set $headers ($csp.reportOnly | ternary "Content-Security-Policy-Report-Only" "Content-Security-Policy") . }}
{{- end }}
{{- range $name, $value := $headers }}
{{- if $value }}
{{- if not (regexMatch "^[a-zA-Z0-9_ :;.,\\\\/\"'?!(){}\\[\\]@<>=+*#$&`|~^%-]+$" (toString $value)) }}
{{- fail (printf "securityHeaders: the value of %s has a character ingress-nginx refuses in a custom header: %s" $name $value) }}
{{- end }}
{{ $name }}: {{ $value | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/* The ConfigMap the annotation names; nothing when disabled. */}}
{{- define "celine-services.securityHeaders.configMap" -}}
{{- if include "celine-services.securityHeaders.enabled" . }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "celine-services.securityHeaders.name" . }}
  labels:
    {{- include "celine-services.labels" . | nindent 4 }}
data:
  {{- include "celine-services.securityHeaders.data" . | nindent 2 }}
{{- with (.Values.securityHeaders | default dict).apiCsp }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "celine-services.securityHeaders.apiName" $ }}
  labels:
    {{- include "celine-services.labels" $ | nindent 4 }}
data:
  {{- include "celine-services.securityHeaders.dataWith" (dict "root" $ "csp" .) | nindent 2 }}
{{- end }}
{{- if include "celine-services.traefik.on" . }}
---
{{ include "celine-services.traefik.headersMiddleware" (dict "root" . "name" (include "celine-services.securityHeaders.name" .) "data" (include "celine-services.securityHeaders.data" .)) }}
{{- with (.Values.securityHeaders | default dict).apiCsp }}
---
{{ include "celine-services.traefik.headersMiddleware" (dict "root" $ "name" (include "celine-services.securityHeaders.apiName" $) "data" (include "celine-services.securityHeaders.dataWith" (dict "root" $ "csp" .))) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}

{{/* The headers Middleware an Ingress chains in Traefik mode; nothing when disabled. */}}
{{- define "celine-services.securityHeaders.middleware" -}}
{{- if include "celine-services.securityHeaders.enabled" . -}}
{{ include "celine-services.securityHeaders.name" . }}
{{- end }}
{{- end }}

{{/* The same for the host's API Ingress (`apiAnnotation`). */}}
{{- define "celine-services.securityHeaders.apiMiddleware" -}}
{{- if include "celine-services.securityHeaders.enabled" . -}}
{{- if (.Values.securityHeaders | default dict).apiCsp -}}
{{ include "celine-services.securityHeaders.apiName" . }}
{{- else -}}
{{ include "celine-services.securityHeaders.name" . }}
{{- end }}
{{- end }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  Traefik mode — `.Values.traefik.enabled` (infra: `ingress_controller.annotations:
  traefik`, defaults/0050-celine-services/ingress-mode.yaml.gotmpl). For a Traefik that
  runs only its Kubernetes Ingress and CRD providers, where nginx.ingress.kubernetes.io/*
  annotations do nothing. What they did becomes Middlewares in the release's namespace,
  chained by `router.middlewares` (a tenant may chain none of another namespace):

    auth-url + auth-signin   traefik.authMiddleware       (oauth2-proxy's root: 302 to sign-in)
    auth-url only            traefik.authCheckMiddleware  (its /oauth2/auth: a bare 401)
    custom-headers           a headers Middleware with the ConfigMap's headers
    permanent-redirect       a redirectRegex Middleware, 302
    rewrite-target /$2       a Prefix path and a stripPrefix Middleware
    proxy-buffer-size, proxy-body-size: nothing (Traefik has neither limit)

  Off (the default), nothing here renders and every Ingress keeps its nginx annotations.

  .Values.traefik:
    enabled              false
    entrypoint           websecure
    clusterIssuer        cert-manager ClusterIssuer; empty: the namespace's `ingress.issuer`
    authMiddleware       oauth2-proxy-auth
    authCheckMiddleware  oauth2-proxy-auth-check
----------------------------------------------------------------------------- */}}

{{- define "celine-services.traefik.on" -}}
{{- if (.Values.traefik | default dict).enabled }}true{{ end }}
{{- end }}

{{/* Annotations of a Traefik-mode Ingress, for (dict "root" . "middlewares" (list <name>…));
     empty names are skipped. */}}
{{- define "celine-services.traefik.annotations" -}}
{{- $root := .root }}
{{- $t := $root.Values.traefik | default dict }}
{{- $issuer := (($root.Values.ingress | default dict).issuer) | default ($root.Values.issuer | default "celine-tls") }}
{{- if $t.clusterIssuer }}
cert-manager.io/cluster-issuer: {{ $t.clusterIssuer }}
{{- else }}
cert-manager.io/issuer: {{ $issuer }}
{{- end }}
traefik.ingress.kubernetes.io/router.entrypoints: {{ $t.entrypoint | default "websecure" }}
{{- $refs := list }}
{{- range .middlewares }}
{{- if . }}{{ $refs = append $refs (printf "%s-%s@kubernetescrd" $root.Release.Namespace .) }}{{ end }}
{{- end }}
{{- with $refs }}
traefik.ingress.kubernetes.io/router.middlewares: {{ join "," . | quote }}
{{- end }}
{{- end }}

{{/* The environment's annotations of an Ingress (`ingress.annotations` and the like),
     without nginx.ingress.kubernetes.io/* in Traefik mode; for (dict "root" . "annotations" <map>). */}}
{{- define "celine-services.traefik.extraAnnotations" -}}
{{- $out := dict }}
{{- range $k, $v := .annotations | default dict }}
{{- if not (and (include "celine-services.traefik.on" $.root) (hasPrefix "nginx.ingress.kubernetes.io/" $k)) }}{{ $_ := set $out $k $v }}{{ end }}
{{- end }}
{{- with $out }}{{ toYaml . }}{{ end }}
{{- end }}

{{- define "celine-services.traefik.authMiddleware" -}}
{{- (.Values.traefik | default dict).authMiddleware | default "oauth2-proxy-auth" }}
{{- end }}

{{- define "celine-services.traefik.authCheckMiddleware" -}}
{{- (.Values.traefik | default dict).authCheckMiddleware | default "oauth2-proxy-auth-check" }}
{{- end }}

{{/* A headers Middleware: (dict "root" . "name" <name> "data" <YAML name: value>). */}}
{{- define "celine-services.traefik.headersMiddleware" -}}
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: {{ .name }}
  labels:
    {{- include "celine-services.labels" .root | nindent 4 }}
spec:
  headers:
    customResponseHeaders:
      {{- .data | nindent 6 }}
{{- end }}

{{/* A redirect Middleware, 302 as the nginx permanent-redirect-code: (dict "root" . "name" <name> "target" <url>). */}}
{{- define "celine-services.traefik.redirectMiddleware" -}}
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: {{ .name }}
  labels:
    {{- include "celine-services.labels" .root | nindent 4 }}
spec:
  redirectRegex:
    regex: "^.*$"
    replacement: {{ .target | quote }}
    permanent: false
{{- end }}

{{/* A stripPrefix Middleware: (dict "root" . "name" <name> "prefixes" (list …)). */}}
{{- define "celine-services.traefik.stripPrefixMiddleware" -}}
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: {{ .name }}
  labels:
    {{- include "celine-services.labels" .root | nindent 4 }}
spec:
  stripPrefix:
    prefixes: {{ .prefixes | toJson }}
{{- end }}


{{/* ----------------------------------------------------------------------------
  Deployment — default used by most services as-is
----------------------------------------------------------------------------- */}}

{{- define "celine-services.deployment" -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "celine-services.fullname" . }}
  labels:
    {{- include "celine-services.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount | default 1 }}
  {{- /* Optional: the rollout strategy, as the Deployment spec takes it (e.g. Recreate for a ReadWriteOnce volume) */}}
  {{- with .Values.strategy }}
  strategy:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "celine-services.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "celine-services.selectorLabels" . | nindent 8 }}
      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
    spec:
      {{- include "celine-services.migrateInitContainer" . | nindent 6 }}
      securityContext:
        runAsNonRoot: true
        runAsUser: {{ .Values.podSecurityContext.runAsUser | default 1000 }}
        {{- with .Values.podSecurityContext.fsGroup }}
        fsGroup: {{ . }}
        {{- end }}
      containers:
        - name: {{ include "celine-services.name" . }}
          image: {{ include "celine-services.image" . | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy | default "IfNotPresent" }}
          {{- /* Optional: for an image that ships more than one entrypoint (e.g. celine-provisioning) */}}
          {{- with .Values.command }}
          command: {{ toJson . }}
          {{- end }}
          {{- with .Values.args }}
          args: {{ toJson . }}
          {{- end }}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
          ports:
            - name: http
              containerPort: {{ .Values.service.port }}
              protocol: TCP
          env:
            - name: LOG_LEVEL
              value: {{ .Values.logLevel | default "INFO" | quote }}
            {{- include "celine-services.postureEnv" . | nindent 12 }}
            {{- include "celine-services.auditEnv" . | nindent 12 }}
            {{- include "celine-services.oidcEnv" . | nindent 12 }}
            {{- include "celine-services.policiesEnv" . | nindent 12 }}
            {{- include "celine-services.postgresEnv" . | nindent 12 }}
            {{- include "celine-services.s3Env" . | nindent 12 }}
            {{- include "celine-services.caEnv" . | nindent 12 }}
            {{- with .Values.extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- if .Values.healthCheck.enabled }}
          livenessProbe:
            httpGet:
              path: {{ .Values.healthCheck.path | default "/health" }}
              port: http
            initialDelaySeconds: {{ .Values.healthCheck.initialDelaySeconds | default 10 }}
            periodSeconds: {{ .Values.healthCheck.periodSeconds | default 30 }}
            timeoutSeconds: {{ .Values.healthCheck.timeoutSeconds | default 5 }}
            failureThreshold: {{ .Values.healthCheck.failureThreshold | default 3 }}
          readinessProbe:
            httpGet:
              path: {{ .Values.healthCheck.path | default "/health" }}
              port: http
            initialDelaySeconds: {{ .Values.healthCheck.initialDelaySeconds | default 5 }}
            periodSeconds: {{ .Values.healthCheck.periodSeconds | default 10 }}
            timeoutSeconds: {{ .Values.healthCheck.timeoutSeconds | default 5 }}
            failureThreshold: {{ .Values.healthCheck.failureThreshold | default 3 }}
          {{- end }}
          volumeMounts:
            {{- include "celine-services.caVolumeMount" . | nindent 12 }}
            {{- with .Values.volumeMounts }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- with .Values.resources }}
          resources:
            {{- toYaml . | nindent 12 }}
          {{- end }}
      volumes:
        {{- include "celine-services.caVolume" . | nindent 8 }}
        {{- with .Values.volumes }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
{{ include "celine-services.podDisruptionBudget" . }}
{{- end }}

{{/* ----------------------------------------------------------------------------
  PodDisruptionBudget — opt-in (.Values.podDisruptionBudget.enabled), over the
  Deployment's pods. The cluster autoscaler does not remove a node whose pods a budget
  does not let it evict, so maxUnavailable 0 (the default) keeps a one-replica service
  through a node scale-down. A rollout is not an eviction: RollingUpdate and Recreate
  roll as before. A drain waits (GKE forces it after an hour on an upgrade).
    podDisruptionBudget:
      enabled: false
      maxUnavailable: 0
----------------------------------------------------------------------------- */}}

{{- define "celine-services.podDisruptionBudget" -}}
{{- $pdb := .Values.podDisruptionBudget | default dict }}
{{- if $pdb.enabled }}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "celine-services.fullname" . }}
  labels:
    {{- include "celine-services.labels" . | nindent 4 }}
spec:
  maxUnavailable: {{ $pdb.maxUnavailable | default 0 }}
  selector:
    matchLabels:
      {{- include "celine-services.selectorLabels" . | nindent 6 }}
{{- end }}
{{- end }}
