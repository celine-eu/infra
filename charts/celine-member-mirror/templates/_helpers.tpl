{{- define "celine-member-mirror.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "celine-member-mirror.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "celine-member-mirror.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "celine-member-mirror.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "celine-member-mirror.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Image reference: `repository:tag`, or `repository:tag@sha256:…` when .Values.imageDigests
lists a digest for that repository and tag. Same rule as celine-services.imageRef.
*/}}
{{- define "celine-member-mirror.imageRef" -}}
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

{{/*
Refuse what must never render: a prod environment signal, a missing source, a missing secret.
*/}}
{{- define "celine-member-mirror.check" -}}
{{- $env := .Values.env | toString | trim | lower }}
{{- if not $env }}
{{- fail "celine-member-mirror: env is required (the environment signal both CLIs read)" }}
{{- end }}
{{- if has $env (list "prod" "production") }}
{{- fail (printf "celine-member-mirror: env is %q; a member mirror never runs in prod" $env) }}
{{- end }}
{{- if not (and .Values.source.url .Values.source.issuer) }}
{{- fail "celine-member-mirror: source.url and source.issuer are required" }}
{{- end }}
{{- if not .Values.registry.authUrl }}
{{- fail "celine-member-mirror: registry.authUrl is required" }}
{{- end }}
{{- range $k := list "sourceClientSecret" "keycloakAdminClientSecret" "registryClientSecret" }}
{{- if not (index $.Values.secrets $k) }}
{{- fail (printf "celine-member-mirror: secrets.%s is required" $k) }}
{{- end }}
{{- end }}
{{- end }}

{{- define "celine-member-mirror.caEnv" -}}
{{- if .Values.caSecret }}
- name: SSL_CERT_FILE
  value: /etc/ssl/celine-ca/ca.crt
- name: REQUESTS_CA_BUNDLE
  value: /etc/ssl/celine-ca/ca.crt
{{- end }}
{{- end }}

{{- define "celine-member-mirror.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: {{ .key }}
{{- end }}
