# SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
# SPDX-License-Identifier: MIT
{{- if .Values.oauth2.enabled }}
apiVersion: apiextensions.crossplane.io/v2
kind: CompositeResourceDefinition
metadata:
  name: oauth2clients.identity.ops.finnes.dev
spec:
  scope: Namespaced
  group: identity.ops.finnes.dev
  names:
    kind: OAuth2Client
    plural: oauth2clients
  defaultCompositionRef:
    name: oauth2client-canaille-ldap
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
              required: [backendRef, clientID, redirectURIs]
              x-kubernetes-validations:
                - rule: "self.tokenEndpointAuthMethod == 'none' ? !has(self.clientSecretRef) : has(self.clientSecretRef)"
                  message: Confidential clients require clientSecretRef; public clients must omit it.
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
                clientID:
                  type: string
                  pattern: '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
                  x-kubernetes-validations:
                    - rule: self == oldSelf
                      message: The client ID is immutable.
                displayName:
                  type: string
                  minLength: 1
                redirectURIs:
                  type: array
                  minItems: 1
                  x-kubernetes-list-type: set
                  items:
                    type: string
                    pattern: '^[A-Za-z][A-Za-z0-9+.-]*://[^[:space:]]+$'
                postLogoutRedirectURIs:
                  type: array
                  x-kubernetes-list-type: set
                  items:
                    type: string
                    pattern: '^[A-Za-z][A-Za-z0-9+.-]*://[^[:space:]]+$'
                scopes:
                  type: array
                  default: [openid, profile, email]
                  minItems: 1
                  x-kubernetes-list-type: set
                  items:
                    type: string
                    minLength: 1
                grantTypes:
                  type: array
                  default: [authorization_code, refresh_token]
                  minItems: 1
                  x-kubernetes-list-type: set
                  items:
                    type: string
                    enum: [authorization_code, refresh_token]
                tokenEndpointAuthMethod:
                  type: string
                  default: client_secret_basic
                  enum: [client_secret_basic, client_secret_post, none]
                clientSecretRef:
                  type: object
                  required: [name, key]
                  properties:
                    name:
                      type: string
                      minLength: 1
                    key:
                      type: string
                      minLength: 1
---
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: oauth2client-canaille-ldap
spec:
  compositeTypeRef:
    apiVersion: identity.ops.finnes.dev/v1alpha1
    kind: OAuth2Client
  mode: Pipeline
  pipeline:
    - step: render-client
      functionRef:
        name: function-go-templating
      input:
        apiVersion: gotemplating.fn.crossplane.io/v1beta1
        kind: GoTemplate
        source: Inline
        inline:
          template: |
{{ .Files.Get "files/oauth2-client.gotmpl" | replace "__CLIENTS_DN_JSON__" (toJson .Values.oauth2.clientsDN) | indent 12 }}
{{- end }}
