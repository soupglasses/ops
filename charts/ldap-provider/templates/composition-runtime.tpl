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
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {{ .Release.Name }}-identity-composer
rules:
  - apiGroups: [ldap.ops.finnes.dev]
    resources: [entries]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [""]
    resources: [secrets]
    verbs: [get, list, watch, create, update, patch, delete]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {{ .Release.Name }}-identity-composer
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: {{ .Release.Name }}-identity-composer
subjects:
  - kind: ServiceAccount
    name: {{ .Values.composition.crossplaneServiceAccount }}
    namespace: {{ .Values.composition.crossplaneNamespace }}
---
# Crossplane's realtime informer watches composed Entry resources across namespaces.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ .Release.Namespace }}-{{ .Release.Name }}-identity-observer
rules:
  - apiGroups: [ldap.ops.finnes.dev]
    resources: [entries]
    verbs: [get, list, watch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {{ .Release.Namespace }}-{{ .Release.Name }}-identity-observer
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {{ .Release.Namespace }}-{{ .Release.Name }}-identity-observer
subjects:
  - kind: ServiceAccount
    name: {{ .Values.composition.crossplaneServiceAccount }}
    namespace: {{ .Values.composition.crossplaneNamespace }}
{{- end }}
