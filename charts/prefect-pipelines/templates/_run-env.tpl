{{- /* One entry of a flow run's env, in Kubernetes form, for job_variables.env.
       A secret is a secretKeyRef that the kubelet resolves when the run's pod starts, so
       its value is never stored in the Prefect deployment or printed by the deploy Job.
       An entry with neither a value nor a secret stays an empty value, as before. */}}
{{- define "prefect-pipelines.runEnv" -}}
{{- if .value }}
- name: {{ .name }}
  value: "{{ .value }}"
{{- else if .secret }}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: {{ default .name .key }}
{{- else }}
- name: {{ .name }}
  value: ""
{{- end }}
{{- end }}
