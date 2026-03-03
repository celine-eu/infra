{{- define "mosquitto-go-auth.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mosquitto-go-auth.fullname" -}}
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

{{- define "mosquitto-go-auth.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "mosquitto-go-auth.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "mosquitto-go-auth.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mosquitto-go-auth.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
  Renders the active backends list for auth_opt_backends.
  jwt is always first. files is appended when enabled.
*/}}
{{- define "mosquitto-go-auth.backends" -}}
{{- $backends := list "jwt" -}}
{{- if .Values.goAuth.files.enabled -}}
{{- $backends = append $backends "files" -}}
{{- end -}}
{{- join "," $backends -}}
{{- end }}

{{/*
  Renders the passwd file content from .Values.users.
  Format: username:hash  (one per line)
*/}}
{{- define "mosquitto-go-auth.passwdFile" -}}
{{- range .Values.users }}
{{- .username }}:{{ .password }}
{{ end -}}
{{- end }}

{{/*
  Renders the ACL file content from .Values.users.
  Format:
    user <username>
    topic <access> <topic>
*/}}
{{- define "mosquitto-go-auth.aclFile" -}}
{{- range .Values.users }}
user {{ .username }}
{{- range .acl }}
topic {{ .access }} {{ .topic }}
{{- end }}

{{ end -}}
{{- end }}
