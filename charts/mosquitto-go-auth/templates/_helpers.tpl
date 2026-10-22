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

{{/*
Image reference: `repository:tag`, or `repository:tag@sha256:…` when .Values.imageDigests
(the environment's `image_digests`) lists a digest for that repository and tag. Keyed by
tag, so a tag bumped without its digest renders unpinned. Same rule as the library chart's
celine-services.imageRef, which this chart does not depend on.
  {{ include "mosquitto-go-auth.imageRef" (dict "repository" r "tag" t "digests" .Values.imageDigests) }}
*/}}
{{- define "mosquitto-go-auth.imageRef" -}}
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
