// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package main

import (
	"testing"

	api "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/apis/namespaced/jellyfin/v1alpha1"
	configapi "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/apis/namespaced/v1beta1"
	xpv2 "github.com/crossplane/crossplane/apis/v2/core/v2"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	clientfake "sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestCredentialsRemainLocalAndRotate(t *testing.T) {
	scheme := runtime.NewScheme()
	_ = corev1.AddToScheme(scheme)
	_ = configapi.AddToScheme(scheme)
	pc := &configapi.ProviderConfig{ObjectMeta: metav1.ObjectMeta{Name: "jellyfin", Namespace: "media"}, Spec: configapi.ProviderConfigSpec{
		Endpoint: "http://jellyfin:8096", Username: "recovery", PasswordSecretRef: configapi.CredentialsReference{Name: "credentials", Key: "password"},
	}}
	secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "credentials", Namespace: "media"}, Data: map[string][]byte{"password": []byte("synthetic-old")}}
	kube := clientfake.NewClientBuilder().WithScheme(scheme).WithObjects(pc, secret).Build()
	obj := &api.PluginConfiguration{ObjectMeta: metav1.ObjectMeta{Name: "ldap", Namespace: "media"}}
	obj.Spec.ProviderConfigReference = &xpv2.ProviderConfigReference{Kind: "ProviderConfig", Name: "jellyfin"}
	for _, password := range []string{"synthetic-old", "synthetic-new"} {
		secret.Data["password"] = []byte(password)
		if err := kube.Update(t.Context(), secret); err != nil {
			t.Fatal(err)
		}
		got, err := setup(t.Context(), kube, obj)
		if err != nil {
			t.Fatal(err)
		}
		if got.Configuration["password"] != password {
			t.Fatal("credential rotation was not observed")
		}
	}
	pc.Spec.Username = ""
	pc.Spec.UsernameSecretRef = &configapi.CredentialsReference{Name: "credentials", Key: "JELLYFIN_ADMIN_USERNAME"}
	secret.Data["JELLYFIN_ADMIN_USERNAME"] = []byte("first-admin")
	if err := kube.Update(t.Context(), pc); err != nil {
		t.Fatal(err)
	}
	if err := kube.Update(t.Context(), secret); err != nil {
		t.Fatal(err)
	}
	configured, err := setup(t.Context(), kube, obj)
	if err != nil {
		t.Fatal(err)
	}
	if configured.Configuration["username"] != "first-admin" {
		t.Fatal("username Secret selector was not resolved")
	}
	obj.SetNamespace("foreign")
	if _, err := setup(t.Context(), kube, obj); err == nil {
		t.Fatal("cross-namespace configuration was accepted")
	}
	obj.SetNamespace("media")
	obj.Spec.ProviderConfigReference.Kind = "ClusterProviderConfig"
	if _, err := setup(t.Context(), kube, obj); err == nil {
		t.Fatal("cluster configuration was accepted")
	}
	obj.Spec.ProviderConfigReference.Kind = "ProviderConfig"
	secret.Data = map[string][]byte{}
	if err := kube.Update(t.Context(), secret); err != nil {
		t.Fatal(err)
	}
	if _, err := setup(t.Context(), kube, obj); err == nil {
		t.Fatal("empty credential was accepted")
	}
}
