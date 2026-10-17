# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ .Release.Name }}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ .Release.Namespace }}-{{ .Release.Name }}
rules:
  - apiGroups: [ldap.ops.finnes.dev]
    resources: [entries, entries/status, entries/finalizers]
    verbs: [get, list, watch, update, patch]
  - apiGroups: [ldap.ops.finnes.dev]
    resources: [providerconfigs, clusterproviderconfigs]
    verbs: [get, list, watch]
  - apiGroups: [""]
    resources: [secrets]
    verbs: [get]
  - apiGroups: [""]
    resources: [namespaces]
    verbs: [get]
  - apiGroups: [""]
    resources: [events]
    verbs: [create, patch, update]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {{ .Release.Namespace }}-{{ .Release.Name }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {{ .Release.Namespace }}-{{ .Release.Name }}
subjects:
  - kind: ServiceAccount
    name: {{ .Release.Name }}
    namespace: {{ .Release.Namespace }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/instance: {{ .Release.Name }}
      app.kubernetes.io/component: controller
  template:
    metadata:
      labels:
        app.kubernetes.io/instance: {{ .Release.Name }}
        app.kubernetes.io/component: controller
    spec:
      automountServiceAccountToken: true
      securityContext:
        seccompProfile:
          type: RuntimeDefault
      serviceAccountName: {{ .Release.Name }}
      containers:
        - name: provider
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args: ["--poll-interval={{ .Values.pollInterval }}"]
          readinessProbe:
            httpGet:
              path: /readyz
              port: 8081
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8081
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 65532
            capabilities:
              drop: [ALL]
