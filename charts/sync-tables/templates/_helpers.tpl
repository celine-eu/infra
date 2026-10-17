{{- define "sync-tables.labels" -}}
app.kubernetes.io/name: sync-tables
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "sync-tables.image" -}}
{{- required "sync-tables: image is required" .Values.image }}
{{- end }}

{{- define "sync-tables.podSpecCommon" -}}
securityContext:
  {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
automountServiceAccountToken: false
{{- end }}

{{- /* Egress to DNS: the selector given, or port 53 anywhere. */}}
{{- define "sync-tables.dnsEgress" -}}
- ports:
    - { protocol: UDP, port: 53 }
    - { protocol: TCP, port: 53 }
  {{- with .Values.dnsSelector }}
  to:
    - namespaceSelector: {}
      podSelector:
        matchLabels:
          {{- toYaml . | nindent 10 }}
  {{- end }}
{{- end }}

{{- /* A role's token Secret: one key per holder, mounted as a directory the tool re-reads.
       Called with (dict "root" $ "name" $name "role" "sink" "v" $s). */}}
{{- define "sync-tables.tokensSecret" -}}
{{- $v := .v }}
{{- if $v.token }}
{{- fail (printf "sync-tables: %s.token is replaced by %s.tokens ({holder: token})" .role .role) }}
{{- end }}
{{- if not $v.tokens }}
{{- fail (printf "sync-tables: %s.tokens must name at least one holder" .role) }}
{{- end }}
{{- range $holder, $token := $v.tokens }}
{{- if not (regexMatch "^[a-z0-9][a-z0-9_-]{0,63}$" $holder) }}
{{- fail (printf "sync-tables: token holder %q is not a plain lower-case name" $holder) }}
{{- end }}
{{- if lt (len $token) 32 }}
{{- fail (printf "sync-tables: the token of %q holds fewer than 32 characters" $holder) }}
{{- end }}
{{- end }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ .name }}-tokens
  labels:
    {{- include "sync-tables.labels" .root | nindent 4 }}
type: Opaque
stringData:
  {{- range $holder, $token := $v.tokens }}
  {{ $holder }}: {{ $token | quote }}
  {{- end }}
{{- end }}

{{- /* The failed-attempt limits as the tool's environment variables. */}}
{{- define "sync-tables.authLimitsEnv" -}}
{{- with .failMax }}
- { name: SYNC_AUTH_FAIL_MAX, value: {{ . | quote }} }
{{- end }}
{{- with .failWindow }}
- { name: SYNC_AUTH_FAIL_WINDOW, value: {{ . | quote }} }
{{- end }}
{{- with .lockout }}
- { name: SYNC_AUTH_LOCKOUT, value: {{ . | quote }} }
{{- end }}
{{- with .failGlobalMax }}
- { name: SYNC_AUTH_FAIL_GLOBAL_MAX, value: {{ . | quote }} }
{{- end }}
{{- end }}
