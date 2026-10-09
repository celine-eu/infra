{{/* A data file's ConfigMap key: its path with "/" written "__" (as celine-onboarding's templates). */}}
{{- define "celine-legal.dataKey" -}}
{{- . | replace "/" "__" }}
{{- end }}

{{/* Every data path, checked; returns nothing, fails on a bad one. */}}
{{- define "celine-legal.validateDataPaths" -}}
{{- range $path, $_ := .Values.data.files }}
{{- if or (hasPrefix "/" $path) (hasPrefix "." $path) (contains "/." $path) (contains "__" $path) (contains "//" $path) }}
{{- fail (printf "data.files: %q is not a relative path without dot segments or \"__\"" $path) }}
{{- end }}
{{- end }}
{{- if not (hasKey .Values.data.files "register.yaml") }}
{{- fail "data.files: no register.yaml; the legal host has nothing to serve (the deployment's private values provide data.files)" }}
{{- end }}
{{- end }}
