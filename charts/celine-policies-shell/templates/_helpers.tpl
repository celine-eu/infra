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
{{- if .Values.keycloak.env }}
- name: CELINE_KEYCLOAK_ENV
  value: {{ .Values.keycloak.env | quote }}
{{- end }}
{{- with .Values.keycloak.bootstrapClient }}
{{- if .clientId }}
- name: CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_ID
  value: {{ .clientId | quote }}
{{- end }}
{{- if .secret }}
- name: CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "celine-policies-shell.fullname" $ }}-bootstrap-client
      key: secret
{{- end }}
{{- end }}
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

{{/*
syncOverlays, checked: a list of {name, content}, in order. A name that is no plain file name,
a name given twice, or an empty overlay stops the render; a map content is written as YAML.
*/}}
{{- define "celine-policies-shell.syncOverlays" -}}
{{- $out := list }}
{{- $seen := dict }}
{{- $in := .Values.syncOverlays | default list }}
{{- if not (kindIs "slice" $in) }}
{{- fail "syncOverlays must be a list of {name, content}" }}
{{- end }}
{{- range $i, $o := $in }}
{{- if not (kindIs "map" $o) }}
{{- fail (printf "syncOverlays[%d] must be {name, content}" $i) }}
{{- end }}
{{- $name := $o.name | default "" | toString }}
{{- if not (regexMatch "^[A-Za-z0-9][A-Za-z0-9._-]*$" $name) }}
{{- fail (printf "syncOverlays[%d]: %q is not a file name (letters, digits, '.', '-' and '_')" $i $name) }}
{{- end }}
{{- if hasKey $seen $name }}
{{- fail (printf "syncOverlays: %s is given twice" $name) }}
{{- end }}
{{- $_ := set $seen $name true }}
{{- $content := $o.content }}
{{- $text := ternary (toYaml $content) (toString ($content | default "")) (kindIs "map" $content) }}
{{- if not (trim $text) }}
{{- fail (printf "syncOverlays: %s is empty" $name) }}
{{- end }}
{{- $out = append $out (dict "name" $name "content" $text) }}
{{- end }}
{{- toYaml $out }}
{{- end }}

{{/*
Image reference: `repository:tag`, or `repository:tag@sha256:…` when .Values.imageDigests
(the environment's `image_digests`) lists a digest for that repository and tag. Keyed by
tag, so a tag bumped without its digest renders unpinned. Same rule as the library chart's
celine-services.imageRef, which this chart does not depend on.
  {{ include "celine-policies-shell.imageRef" (dict "repository" r "tag" t "digests" .Values.imageDigests) }}
*/}}
{{- define "celine-policies-shell.imageRef" -}}
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
