# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
{{- if .Values.tests.enabled }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ .Release.Name }}-tests
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {{ .Release.Name }}-tests
rules:
  - apiGroups: [apps]
    resources: [deployments]
    verbs: [get]
{{- if eq .Values.tests.mode "integration" }}
  - apiGroups: [ldap.ops.finnes.dev]
    resources: [entries, providerconfigs]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [identity.ops.finnes.dev]
    resources: [oauth2clients]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [""]
    resources: [secrets]
    verbs: [get, list, create, update, delete]
  - apiGroups: [""]
    resources: [pods]
    verbs: [get, list, delete]
{{- end }}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {{ .Release.Name }}-tests
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: {{ .Release.Name }}-tests
subjects:
  - kind: ServiceAccount
    name: {{ .Release.Name }}-tests
    namespace: {{ .Release.Namespace }}
---
apiVersion: v1
kind: Pod
metadata:
  name: {{ .Release.Name }}-tests
  annotations:
    helm.sh/hook: test
    helm.sh/hook-delete-policy: before-hook-creation
spec:
  restartPolicy: Never
  serviceAccountName: {{ .Release.Name }}-tests
  containers:
    - name: tests
      image: "{{ .Values.tests.image.repository }}:{{ .Values.tests.image.tag }}"
      imagePullPolicy: {{ .Values.tests.image.pullPolicy }}
      args:
        - -test.v
        - -test.timeout=8m
        - {{ ternary "-test.run=^TestHelmReadiness$" "-test.run=Test" (eq .Values.tests.mode "smoke") | quote }}
      env:
        - name: POD_NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        - name: TEST_POD_IP
          valueFrom:
            fieldRef:
              fieldPath: status.podIP
        - name: OAUTH2_ENABLED
          value: {{ .Values.oauth2.enabled | quote }}
        - name: HELM_RELEASE
          value: {{ .Release.Name }}
        - name: CANAILLE_SCHEMA_LDIF
          value: /fixtures/oauth2-openldap.ldif
        - name: CANAILLE_TEST_PYTHON
          value: /usr/bin/python3
      resources:
        requests:
          cpu: 100m
          memory: 128Mi
        limits:
          memory: 512Mi
{{- end }}
