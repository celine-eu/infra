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

{{- define "celine-services.selectorLabels" -}}
app.kubernetes.io/name: {{ include "celine-services.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
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
    image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default "latest" }}"
    imagePullPolicy: {{ .Values.image.pullPolicy | default "IfNotPresent" }}
    command: {{ .Values.migrate.command | default (list "alembic" "upgrade" "head") | toJson }}
    securityContext:
      allowPrivilegeEscalation: false
      capabilities:
        drop: [ALL]
    env:
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
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "celine-services.fullname" . }}
  labels:
    {{- include "celine-services.labels" . | nindent 4 }}
  annotations:
    cert-manager.io/issuer: {{ .Values.ingress.issuer | default "celine-tls" }}
    nginx.ingress.kubernetes.io/rewrite-target: /
    {{- if .Values.ingress.auth.enabled }}
    nginx.ingress.kubernetes.io/auth-url: "https://sso.{{ .Values.ingress.domain }}/oauth2/auth"
    nginx.ingress.kubernetes.io/auth-signin: "https://sso.{{ .Values.ingress.domain }}/oauth2/start?rd=$scheme://$host$request_uri"
    nginx.ingress.kubernetes.io/auth-response-headers: "Authorization, X-Auth-Request-Email, X-Auth-Request-User, X-Auth-Request-Access-Token"
    {{- end }}
    {{- with .Values.ingress.annotations }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
spec:
  ingressClassName: nginx
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
{{- end }}
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
      containers:
        - name: {{ include "celine-services.name" . }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default "latest" }}"
          imagePullPolicy: {{ .Values.image.pullPolicy | default "IfNotPresent" }}
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
            failureThreshold: {{ .Values.healthCheck.failureThreshold | default 3 }}
          readinessProbe:
            httpGet:
              path: {{ .Values.healthCheck.path | default "/health" }}
              port: http
            initialDelaySeconds: {{ .Values.healthCheck.initialDelaySeconds | default 5 }}
            periodSeconds: {{ .Values.healthCheck.periodSeconds | default 10 }}
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
{{- end }}
