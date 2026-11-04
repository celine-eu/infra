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

{{/*
One push source (Secret, profile ConfigMap, CronJob, NetworkPolicy), from
(dict "root" $ "p" <pushSource-shaped values> "name" <resource name>).
*/}}
{{- define "sync-tables.pushSource" -}}
{{- $root := .root }}
{{- $p := .p }}
{{- $name := .name }}
{{- if not (hasPrefix "wss://" (required "sync-tables: pushSource.sinkUrl is required" $p.sinkUrl)) }}
{{- fail (printf "sync-tables: pushSource.sinkUrl must be wss://, got %s" $p.sinkUrl) }}
{{- end }}
---
# The client side of the mutual TLS, and the token. The source trusts the sync CA only.
apiVersion: v1
kind: Secret
metadata:
  name: {{ $name }}-tls
  labels:
    {{- include "sync-tables.labels" $root | nindent 4 }}
type: Opaque
stringData:
  ca.crt: {{ required "sync-tables: pushSource.ca is required" $p.ca | quote }}
  tls.crt: {{ required "sync-tables: pushSource.clientCert is required" $p.clientCert | quote }}
  tls.key: {{ required "sync-tables: pushSource.clientKey is required" $p.clientKey | quote }}
  token: {{ required "sync-tables: pushSource.token is required" $p.token | quote }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ $name }}-profile
  labels:
    {{- include "sync-tables.labels" $root | nindent 4 }}
data:
  profile.yaml: |
    {{- required "sync-tables: pushSource.profile is required" $p.profile | nindent 4 }}
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ $name }}
  labels:
    {{- include "sync-tables.labels" $root | nindent 4 }}
    app.kubernetes.io/component: push-source
spec:
  schedule: {{ $p.schedule | quote }}
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 300
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      # Every step is idempotent and the next run picks up where this one stopped, so a
      # failed run is reported, not retried: exit 1 = some tables failed, 2 = refused.
      backoffLimit: 0
      activeDeadlineSeconds: {{ $p.activeDeadlineSeconds }}
      template:
        metadata:
          labels:
            {{- include "sync-tables.labels" $root | nindent 12 }}
            app.kubernetes.io/component: push-source
          annotations:
            checksum/profile: {{ $p.profile | sha256sum }}
        spec:
          restartPolicy: Never
          {{- include "sync-tables.podSpecCommon" $root | nindent 10 }}
          containers:
            - name: push-source
              image: {{ include "sync-tables.image" $root }}
              imagePullPolicy: {{ $root.Values.imagePullPolicy }}
              args: ["--from", {{ $p.from | quote }}, "--to", {{ $p.to | quote }}, "--profile", "/etc/sync/profile.yaml"]
              env:
                - name: SYNC_SINK_URL
                  value: {{ $p.sinkUrl | quote }}
                - name: SYNC_TLS_CA
                  value: /run/sync/ca.crt
                - name: SYNC_TLS_CERT
                  value: /run/sync/tls.crt
                - name: SYNC_TLS_KEY
                  value: /run/sync/tls.key
                - name: SYNC_TOKEN_FILE
                  value: /run/sync/token
                - name: DB_DEV
                  valueFrom:
                    secretKeyRef:
                      name: {{ required "sync-tables: pushSource.sourceSecret.name is required" $p.sourceSecret.name }}
                      key: {{ $p.sourceSecret.key }}
              securityContext:
                {{- toYaml $root.Values.securityContext | nindent 16 }}
              {{- with $p.resources }}
              resources:
                {{- toYaml . | nindent 16 }}
              {{- end }}
              volumeMounts:
                - { name: tls, mountPath: /run/sync, readOnly: true }
                - { name: profile, mountPath: /etc/sync, readOnly: true }
                - { name: tmp, mountPath: /tmp }
          volumes:
            - name: tls
              secret:
                secretName: {{ $name }}-tls
                defaultMode: 0440
            - name: profile
              configMap:
                name: {{ $name }}-profile
            - name: tmp
              emptyDir: {}
{{- if $p.networkPolicy.enabled }}
---
# In: nothing. Out: DNS, the source database, and HTTPS to the sink.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ $name }}
  labels:
    {{- include "sync-tables.labels" $root | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: sync-tables
      app.kubernetes.io/instance: {{ $root.Release.Name }}
      app.kubernetes.io/component: push-source
  policyTypes: [Ingress, Egress]
  egress:
    {{- include "sync-tables.dnsEgress" $root | nindent 4 }}
    - to:
        - podSelector:
            matchLabels:
              {{- toYaml $p.networkPolicy.databasePodSelector | nindent 14 }}
      ports:
        - { protocol: TCP, port: 5432 }
    - ports:
        - { protocol: TCP, port: 443 }
{{- end }}
{{- end }}
