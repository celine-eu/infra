{{/*
Expand the name of the chart.
*/}}
{{- define "celine-dataset-api-shell.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "celine-dataset-api-shell.fullname" -}}
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
{{- define "celine-dataset-api-shell.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "celine-dataset-api-shell.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "celine-dataset-api-shell.selectorLabels" -}}
app.kubernetes.io/name: {{ include "celine-dataset-api-shell.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Image reference: `repository:tag`, or `repository:tag@sha256:…` when .Values.imageDigests
(the environment's `image_digests`) lists a digest for that repository and tag. Keyed by
tag, so a tag bumped without its digest renders unpinned. Same rule as the library chart's
celine-services.imageRef, which this chart does not depend on.
  {{ include "celine-dataset-api-shell.imageRef" (dict "repository" r "tag" t "digests" .Values.imageDigests) }}
*/}}
{{- define "celine-dataset-api-shell.imageRef" -}}
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
