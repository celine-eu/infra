{{/*
Expand the name of the chart.
*/}}
{{- define "celine-policies-shell.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "celine-policies-shell.fullname" -}}
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

{{/*
Common labels
*/}}
{{- define "celine-policies-shell.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "celine-policies-shell.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "celine-policies-shell.selectorLabels" -}}
app.kubernetes.io/name: {{ include "celine-policies-shell.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Keycloak connection (CELINE_KEYCLOAK_* → KeycloakSettings), shared by the shell and the
bootstrap init container.
*/}}
{{- define "celine-policies-shell.keycloakEnv" -}}
- name: CELINE_KEYCLOAK_BASE_URL
  value: {{ .Values.keycloak.baseUrl | quote }}
- name: CELINE_KEYCLOAK_REALM
  value: {{ .Values.keycloak.realm | quote }}
{{- if .Values.keycloak.adminUser }}
- name: CELINE_KEYCLOAK_ADMIN_USER
  value: {{ .Values.keycloak.adminUser | quote }}
{{- end }}
{{- if .Values.keycloak.existingSecret }}
{{- if .Values.keycloak.adminPasswordKey }}
- name: CELINE_KEYCLOAK_ADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.keycloak.existingSecret }}
      key: {{ .Values.keycloak.adminPasswordKey }}
{{- end }}
{{- if .Values.keycloak.adminClientSecretKey }}
- name: CELINE_KEYCLOAK_ADMIN_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.keycloak.existingSecret }}
      key: {{ .Values.keycloak.adminClientSecretKey }}
{{- end }}
{{- end }}
{{- end }}

{{/*
The CA bundle variables from `extraEnv`, for the init container: a self-signed Keycloak is
reached over TLS from there too.
*/}}
{{- define "celine-policies-shell.caEnv" -}}
{{- range .Values.extraEnv }}
{{- if has .name (list "SSL_CERT_FILE" "REQUESTS_CA_BUNDLE") }}
- name: {{ .name }}
  value: {{ .value | quote }}
{{- end }}
{{- end }}
{{- end }}
