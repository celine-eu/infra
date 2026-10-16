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
