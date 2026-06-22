{{- define "studio.name" -}}studio{{- end -}}
{{- define "studio.labels" -}}
app.kubernetes.io/name: {{ include "studio.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
