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
Keycloak connection (CELINE_KEYCLOAK_* → KeycloakSettings). Called with
(dict "root" $ "master" bool): `master` adds the master realm's credentials, the bootstrap
client's secret and the master administrator's password. The bootstrap init container always
gets them; the shell only while shell.masterCredentials is on.
*/}}
{{- define "celine-policies-shell.keycloakEnv" -}}
{{- $root := .root }}
{{- $master := .master }}
{{- $kc := $root.Values.keycloak -}}
- name: CELINE_KEYCLOAK_BASE_URL
  value: {{ $kc.baseUrl | quote }}
- name: CELINE_KEYCLOAK_REALM
  value: {{ $kc.realm | quote }}
{{- if $kc.env }}
- name: CELINE_KEYCLOAK_ENV
  value: {{ $kc.env | quote }}
{{- end }}
{{- with $kc.bootstrapClient }}
{{- if .clientId }}
- name: CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_ID
  value: {{ .clientId | quote }}
{{- end }}
{{- if and $master .secret }}
- name: CELINE_KEYCLOAK_BOOTSTRAP_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "celine-policies-shell.fullname" $root }}-bootstrap-client
      key: secret
{{- end }}
{{- end }}
{{- if and $master $kc.adminUser }}
- name: CELINE_KEYCLOAK_ADMIN_USER
  value: {{ $kc.adminUser | quote }}
{{- end }}
{{- if $kc.adminPassword }}
{{- if $master }}
- name: CELINE_KEYCLOAK_ADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "celine-policies-shell.fullname" $root }}-keycloak-admin
      key: password
{{- end }}
{{- else if $kc.existingSecret }}
{{- if and $master $kc.adminPasswordKey }}
- name: CELINE_KEYCLOAK_ADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $kc.existingSecret }}
      key: {{ $kc.adminPasswordKey }}
{{- end }}
{{- if $kc.adminClientSecretKey }}
- name: CELINE_KEYCLOAK_ADMIN_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $kc.existingSecret }}
      key: {{ $kc.adminClientSecretKey }}
{{- end }}
{{- end }}
{{- end }}

{{/*
"true" when the shell holds no master credential (shell.masterCredentials off, bootstrap on):
it signs in as the realm's own admin client, celine-admin-cli, whose secret the bootstrap init
container records in a memory volume both containers mount. Empty otherwise.
*/}}
{{- define "celine-policies-shell.realmScopedShell" -}}
{{- if and (not .Values.shell.masterCredentials) .Values.bootstrap.enabled }}true{{ end }}
{{- end }}

{{/*
The secrets file bootstrap records celine-admin-cli's secret in and the shell's sync reads:
the shared memory volume for a realm-scoped shell, each container's own /tmp otherwise.
*/}}
{{- define "celine-policies-shell.secretsFile" -}}
{{- if include "celine-policies-shell.realmScopedShell" . }}/run/celine-policies-secrets/secrets.yaml{{ else }}/tmp/.secrets.yaml{{ end }}
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
bootstrap.platformAdminPasswords as the variables celine-policies reads (REQ-0022): `template`
→ CELINE_KEYCLOAK_PLATFORM_ADMIN_PASSWORD_TEMPLATE (must contain {username}), `users`
({username: password}) → CELINE_KEYCLOAK_PLATFORM_ADMIN_PASSWORD_<USERNAME>. Empty map: none.
*/}}
{{- define "celine-policies-shell.platformAdminPasswordEnv" -}}
{{- $p := .Values.bootstrap.platformAdminPasswords | default dict }}
{{- $out := dict }}
{{- with $p.template }}
{{- if not (contains "{username}" (toString .)) }}
{{- fail "bootstrap.platformAdminPasswords.template must contain {username}: without it every account gets the same password" }}
{{- end }}
{{- $_ := set $out "CELINE_KEYCLOAK_PLATFORM_ADMIN_PASSWORD_TEMPLATE" (toString .) }}
{{- end }}
{{- range $user, $pw := ($p.users | default dict) }}
{{- if $pw }}
{{- $_ := set $out (printf "CELINE_KEYCLOAK_PLATFORM_ADMIN_PASSWORD_%s" (regexReplaceAll "[^A-Za-z0-9]" $user "_" | upper)) (toString $pw) }}
{{- end }}
{{- end }}
{{- toYaml $out }}
{{- end }}

{{/*
syncBase, checked: {name, content} or empty. A name that is no plain file name, an empty
content, or `clientsFile` set as well stops the render; a map content is written as YAML.
*/}}
{{- define "celine-policies-shell.syncBase" -}}
{{- $b := .Values.syncBase | default dict }}
{{- if not (kindIs "map" $b) }}
{{- fail "syncBase must be {name, content}" }}
{{- end }}
{{- if $b }}
{{- $name := $b.name | default "" | toString }}
{{- if not (regexMatch "^[A-Za-z0-9][A-Za-z0-9._-]*$" $name) }}
{{- fail (printf "syncBase: %q is not a file name (letters, digits, '.', '-' and '_')" $name) }}
{{- end }}
{{- if .Values.clientsFile }}
{{- fail "syncBase and clientsFile name the same thing: set one" }}
{{- end }}
{{- $content := $b.content }}
{{- $text := ternary (toYaml $content) (toString ($content | default "")) (kindIs "map" $content) }}
{{- if not (trim $text) }}
{{- fail (printf "syncBase: %s is empty" $name) }}
{{- end }}
{{- toYaml (dict "name" $name "content" $text) }}
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
