# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
{{- if or .Values.oauth2.enabled .Values.groups.enabled }}
apiVersion: pkg.crossplane.io/v1beta1
kind: DeploymentRuntimeConfig
metadata:
  name: {{ .Release.Namespace }}-{{ .Release.Name }}-function-restricted
spec:
  deploymentTemplate:
    spec:
      selector: {}
      template:
        spec:
          securityContext:
            runAsNonRoot: true
            runAsUser: 65532
            runAsGroup: 65532
            seccompProfile:
              type: RuntimeDefault
          containers:
            - name: package-runtime
              securityContext:
                allowPrivilegeEscalation: false
                runAsNonRoot: true
                capabilities:
                  drop: [ALL]
---
apiVersion: pkg.crossplane.io/v1
kind: Function
metadata:
  name: function-go-templating
spec:
  package: {{ .Values.composition.functionPackage | quote }}
  runtimeConfigRef:
    name: {{ .Release.Namespace }}-{{ .Release.Name }}-function-restricted
---
# This API is installed by Helm, so Crossplane's package RBAC manager does not grant access.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ .Release.Namespace }}-{{ .Release.Name }}-identity-composer
  labels:
    rbac.crossplane.io/aggregate-to-crossplane: "true"
rules:
  - apiGroups: [ldap.ops.finnes.dev]
    resources: [entries]
    verbs: [get, list, watch, create, update, patch, delete]
{{- end }}
