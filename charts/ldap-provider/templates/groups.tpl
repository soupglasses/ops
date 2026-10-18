# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
{{- if .Values.groups.enabled }}
apiVersion: apiextensions.crossplane.io/v2
kind: CompositeResourceDefinition
metadata:
  name: groupofnames.identity.ops.finnes.dev
spec:
  scope: Namespaced
  group: identity.ops.finnes.dev
  names:
    kind: GroupOfNames
    plural: groupofnames
  defaultCompositionRef:
    name: groupofnames-ldap
  versions:
    - name: v1alpha1
      served: true
      referenceable: true
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              required: [backendRef, cn]
              properties:
                backendRef:
                  type: object
                  required: [name]
                  x-kubernetes-validations:
                    - rule: self == oldSelf
                      message: A backend change requires deliberate replacement and adoption.
                  properties:
                    name:
                      type: string
                      minLength: 1
                    kind:
                      type: string
                      enum: [ProviderConfig, ClusterProviderConfig]
                      default: ClusterProviderConfig
                cn:
                  type: string
                  pattern: '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
                  x-kubernetes-validations:
                    - rule: self == oldSelf
                      message: The group name is immutable.
                description:
                  type: string

---
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: groupofnames-ldap
spec:
  compositeTypeRef:
    apiVersion: identity.ops.finnes.dev/v1alpha1
    kind: GroupOfNames
  mode: Pipeline
  pipeline:
    - step: render-group
      functionRef:
        name: function-go-templating
      input:
        apiVersion: gotemplating.fn.crossplane.io/v1beta1
        kind: GoTemplate
        source: Inline
        inline:
          template: |
{{ .Files.Get "files/groupofnames.gotmpl" | replace "__GROUPS_DN_JSON__" (toJson .Values.groups.baseDN) | replace "__SENTINEL_DN_JSON__" (toJson .Values.groups.sentinelDN) | indent 12 }}
{{- end }}
