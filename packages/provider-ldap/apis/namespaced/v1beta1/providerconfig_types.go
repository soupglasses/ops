// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

// +kubebuilder:object:generate=true
// +groupName=ldap.ops.finnes.dev
package v1beta1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/scheme"
)

type CredentialsReference struct {
	// Namespace is required for cluster configurations and local for namespaced configurations.
	Namespace string `json:"namespace,omitempty"`
	Name      string `json:"name"`
	Key       string `json:"key"`
}
type ProviderConfigSpec struct {
	Host   string `json:"host"`
	Port   int    `json:"port"`
	BindDN string `json:"bindDN"`
	// TLSServerName overrides certificate verification when connecting through a Service name.
	TLSServerName string `json:"tlsServerName,omitempty"`
	// +kubebuilder:default=true
	TLS                  bool                 `json:"tls"`
	CredentialsSecretRef CredentialsReference `json:"credentialsSecretRef"`
}

// +kubebuilder:object:root=true
// +kubebuilder:resource:scope=Namespaced
// +kubebuilder:subresource:status
type ProviderConfig struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`
	Spec              ProviderConfigSpec `json:"spec"`
}

// +kubebuilder:object:root=true
type ProviderConfigList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []ProviderConfig `json:"items"`
}

// +kubebuilder:object:root=true
// +kubebuilder:resource:scope=Cluster
// +kubebuilder:subresource:status
type ClusterProviderConfig struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`
	Spec              ClusterProviderConfigSpec `json:"spec"`
}

type ClusterProviderConfigSpec struct {
	ProviderConfigSpec `json:",inline"`
	// Entries must be direct children of one of these DNs and use a namespace-prefixed RDN.
	// +kubebuilder:validation:MinItems=1
	AllowedBases []string `json:"allowedBases"`
}

// +kubebuilder:object:root=true
type ClusterProviderConfigList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []ClusterProviderConfig `json:"items"`
}

var SchemeBuilder = &scheme.Builder{GroupVersion: schema.GroupVersion{Group: "ldap.ops.finnes.dev", Version: "v1beta1"}}
var AddToScheme = SchemeBuilder.AddToScheme

func init() {
	SchemeBuilder.Register(&ProviderConfig{}, &ProviderConfigList{}, &ClusterProviderConfig{}, &ClusterProviderConfigList{})
}
