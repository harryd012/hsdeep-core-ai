{{/*
Expand the chart name.
*/}}
{{- define "hsdeep.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name: <release>-<chart> unless fullnameOverride is set.
*/}}
{{- define "hsdeep.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "hsdeep.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Chart name and version label.
*/}}
{{- define "hsdeep.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels applied to every resource.
*/}}
{{- define "hsdeep.labels" -}}
helm.sh/chart: {{ include "hsdeep.chart" . }}
app.kubernetes.io/part-of: hsdeep-core-ai
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- if .Values.nameOverride }}
app.kubernetes.io/name: {{ .Values.nameOverride }}
{{- else }}
app.kubernetes.io/name: {{ .Chart.Name }}
{{- end }}
{{- end -}}

{{/*
Per-component name: <fullname>-<component>.
*/}}
{{- define "hsdeep.componentName" -}}
{{- printf "%s-%s" (include "hsdeep.fullname" .) .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Name of the Secret holding application credentials. Uses an operator-provided
existingSecret when set; otherwise the chart-managed Secret.
*/}}
{{- define "hsdeep.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "hsdeep.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Name of the Secret holding the Postgres credentials.
*/}}
{{- define "hsdeep.postgresSecretName" -}}
{{- if .Values.postgres.auth.existingSecret -}}
{{- .Values.postgres.auth.existingSecret -}}
{{- else -}}
{{- printf "%s-postgres-auth" (include "hsdeep.fullname" .) -}}
{{- end -}}
{{- end -}}
