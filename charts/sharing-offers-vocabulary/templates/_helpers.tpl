{{- define "sharing-offers-vocabulary.labels" -}}
app.kubernetes.io/name: sharing-offers-vocabulary
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "sharing-offers-vocabulary.selector" -}}
app.kubernetes.io/name: sharing-offers-vocabulary
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Image reference: `repository:tag`, or `repository:tag@sha256:…` when .Values.imageDigests
(the environment's `image_digests`) lists a digest for that repository and tag. Same rule
as the library chart's celine-services.imageRef, which this chart does not depend on.
*/}}
{{- define "sharing-offers-vocabulary.imageRef" -}}
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
