// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

// +kubebuilder:object:generate=true
// +groupName=jellyfin.ops.finnes.dev
package v1beta1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/scheme"
)

type CredentialsReference struct {
	Name string `json:"name"`
	Key  string `json:"key"`
}

// +kubebuilder:validation:XValidation:rule="has(self.username) != has(self.usernameSecretRef)",message="set username or usernameSecretRef"
type ProviderConfigSpec struct {
	// +kubebuilder:validation:Pattern=`^https?://[^/]+.*$`
	Endpoint          string                `json:"endpoint"`
	Username          string                `json:"username,omitempty"`
	UsernameSecretRef *CredentialsReference `json:"usernameSecretRef,omitempty"`
	PasswordSecretRef CredentialsReference  `json:"passwordSecretRef"`
}

// +kubebuilder:object:root=true
// +kubebuilder:resource:scope=Namespaced
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

var SchemeBuilder = &scheme.Builder{GroupVersion: schema.GroupVersion{Group: "jellyfin.ops.finnes.dev", Version: "v1beta1"}}
var AddToScheme = SchemeBuilder.AddToScheme

func init() { SchemeBuilder.Register(&ProviderConfig{}, &ProviderConfigList{}) }
