{{/*
==============================================================================
  celine-onboarding — what the library does not know about this service.

  The Deployment and Secret are still the library's: each template below builds
  a copy of .Values with the onboarding env, volumes and secret keys added, and
  hands that copy to the library include. So a library change still lands here.
==============================================================================
*/}}

{{/* Refuse a configuration that would run but record the wrong thing. */}}
{{- define "celine-onboarding.validate" -}}
{{- $o := .Values.onboarding }}
{{- $ips := $o.forwardedAllowIps | default "" | toString | trim }}
{{- if not $ips }}
{{- fail "onboarding.forwardedAllowIps is required: the ingress controller's pod address range (onboarding README, \"Security\")" }}
{{- end }}
{{- if or (eq $ips "*") (has "*" (splitList "," ($ips | nospace))) }}
{{- fail "onboarding.forwardedAllowIps must not be \"*\": any caller reaching the port would choose its own address" }}
{{- end }}
{{- if not (regexMatch "^[0-9a-fA-F:.,/ ]+$" $ips) }}
{{- fail (printf "onboarding.forwardedAllowIps must be addresses or CIDR ranges, comma-separated (got %q)" $ips) }}
{{- end }}
{{- if not $o.encryptionKey }}
{{- fail "onboarding.encryptionKey is required (a Fernet key; see the onboarding README)" }}
{{- end }}
{{- end }}

{{/* The ConfigMap key for a template path: `/` is not allowed in a key. */}}
{{- define "celine-onboarding.templateKey" -}}
{{- . | replace "/" "__" }}
{{- end }}

{{/* Every template path, checked; returns nothing, fails on a bad one. */}}
{{- define "celine-onboarding.validateTemplatePaths" -}}
{{- $seen := dict }}
{{- $paths := concat (keys (.Values.templates.files | default dict)) (keys (.Values.templates.binaryFiles | default dict)) }}
{{- range $paths }}
{{- if not (regexMatch "^[A-Za-z0-9_][A-Za-z0-9._-]*(/[A-Za-z0-9_][A-Za-z0-9._-]*)+$" .) }}
{{- fail (printf "templates: %q is not a path of the form <slug>/<file>, with no leading dot, empty or \"..\" segment" .) }}
{{- end }}
{{- $key := include "celine-onboarding.templateKey" . }}
{{- if hasKey $seen $key }}
{{- fail (printf "templates: %q is given twice, or collides with %q" . (get $seen $key)) }}
{{- end }}
{{- $_ := set $seen $key . }}
{{- end }}
{{- end }}

{{- define "celine-onboarding.hasTemplates" -}}
{{- if or .Values.templates.files .Values.templates.binaryFiles }}true{{- end }}
{{- end }}

{{- define "celine-onboarding.templatesConfigMapName" -}}
{{- printf "%s-rec-templates" (include "celine-services.fullname" .) }}
{{- end }}

{{/* Plain and secret-backed env for the service's own flat settings. */}}
{{- define "celine-onboarding.env" -}}
{{- $o := .Values.onboarding }}
{{- $secretName := include "celine-services.secretName" . }}
- name: OIDC_BASE_URL
  value: {{ .Values.oidc.baseUrl | quote }}
- name: OIDC_JWKS_URI
  value: {{ .Values.oidc.jwksUri | quote }}
- name: OIDC_AUDIENCE
  value: {{ .Values.oidc.service.audience | quote }}
- name: OIDC_CLIENT_ID
  value: {{ .Values.oidc.service.clientId | quote }}
- name: OIDC_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: CELINE_OIDC_CLIENT_SECRET
- name: ONBOARDING_CLI_CLIENT_ID
  value: {{ $o.cliClientId | quote }}
- name: ONBOARDING_CLI_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: ONBOARDING_CLI_CLIENT_SECRET
- name: ONBOARDING_API_URL
  value: {{ $o.apiUrl | quote }}
- name: ENCRYPTION_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: ENCRYPTION_KEY
- name: REQUIRE_ENCRYPTION
  value: "true"
- name: FORWARDED_ALLOW_IPS
  value: {{ $o.forwardedAllowIps | nospace | quote }}
- name: DATASPACE_ENABLED
  value: {{ $o.dataspaceEnabled | toString | quote }}
{{- if $o.dataspaceEnabled }}
- name: IDENTITY_REGISTRY_URL
  value: {{ $o.identityRegistryUrl | quote }}
- name: DS_ONBOARDING_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: DS_ONBOARDING_CLIENT_SECRET
{{- end }}
{{- if $o.dsConnectorUrl }}
- name: DS_CONNECTOR_URL
  value: {{ $o.dsConnectorUrl | quote }}
{{- end }}
- name: REC_REGISTRY_URL
  value: {{ $o.recRegistryUrl | quote }}
- name: DIGITAL_TWIN_URL
  value: {{ $o.digitalTwinUrl | quote }}
- name: PROVISIONING_URL
  value: {{ $o.provisioningUrl | quote }}
- name: CORS_ORIGINS
  value: {{ $o.corsOrigins | quote }}
- name: EXTRACTION_ENABLED
  value: {{ $o.extractionEnabled | toString | quote }}
{{- $llm := $o.llm | default dict }}
{{- if $llm.baseUrl }}
- name: LLM_BASE_URL
  value: {{ $llm.baseUrl | quote }}
- name: LLM_VISION_MODEL
  value: {{ $llm.visionModel | default "" | quote }}
{{- if $llm.apiKey }}
- name: LLM_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: LLM_API_KEY
{{- end }}
{{- end }}
- name: SMS_PROVIDER
  value: {{ $o.smsProvider | default "none" | quote }}
- name: DPA_SMS_SIGNED
  value: {{ $o.dpaSmsSigned | default false | toString | quote }}
{{- if eq ($o.smsProvider | default "" | lower) "brevo" }}
- name: BREVO_SMS_SENDER
  value: {{ $o.brevoSmsSender | default "" | quote }}
- name: BREVO_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: BREVO_API_KEY
{{- end }}
- name: SMTP_HOST
  value: {{ $o.smtp.host | default "" | quote }}
{{- if $o.smtp.host }}
- name: SMTP_PORT
  value: {{ $o.smtp.port | default 587 | toString | quote }}
- name: SMTP_USER
  value: {{ $o.smtp.user | default "" | quote }}
- name: SMTP_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: SMTP_PASSWORD
- name: SMTP_TLS
  value: {{ $o.smtp.tls | toString | quote }}
{{- with $o.smtp.from }}
- name: SMTP_FROM
  value: {{ . | quote }}
{{- end }}
{{- with $o.smtp.notify }}
- name: SMTP_NOTIFY
  value: {{ . | quote }}
{{- end }}
{{- end }}
- name: TEMPLATES_DIR
  value: {{ .Values.templates.mountPath | quote }}
- name: DATA_DIR
  value: {{ .Values.persistence.mountPath | quote }}
- name: POLICIES_DIR
  value: "/app/policies"
{{- end }}

{{/* The keys the chart's Secret carries besides the library's. */}}
{{- define "celine-onboarding.secretData" -}}
{{- $o := .Values.onboarding }}
{{- $data := dict "ENCRYPTION_KEY" ($o.encryptionKey | toString) "ONBOARDING_CLI_CLIENT_SECRET" ($o.cliClientSecret | toString) }}
{{- if $o.smtp.host }}
{{- $_ := set $data "SMTP_PASSWORD" ($o.smtp.password | default "" | toString) }}
{{- end }}
{{- if eq ($o.smsProvider | default "" | lower) "brevo" }}
{{- $_ := set $data "BREVO_API_KEY" ($o.brevoApiKey | default "" | toString) }}
{{- end }}
{{- if and $o.llm $o.llm.baseUrl $o.llm.apiKey }}
{{- $_ := set $data "LLM_API_KEY" ($o.llm.apiKey | toString) }}
{{- end }}
{{- if $o.dataspaceEnabled }}
{{- $_ := set $data "DS_ONBOARDING_CLIENT_SECRET" ($o.dsOnboardingClientSecret | default "" | toString) }}
{{- end }}
{{- toYaml (merge $data (.Values.extraSecrets | default dict)) }}
{{- end }}
