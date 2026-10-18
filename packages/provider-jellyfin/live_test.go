//go:build integration

// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package crossplane

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	api "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/apis/namespaced/jellyfin/v1alpha1"
	localconfig "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/config"
	"github.com/crossplane/crossplane-runtime/v2/pkg/logging"
	"github.com/crossplane/crossplane-runtime/v2/pkg/meta"
	xpresource "github.com/crossplane/crossplane-runtime/v2/pkg/resource"
	xpv2 "github.com/crossplane/crossplane/apis/v2/core/v2"
	"github.com/crossplane/upjet/v2/pkg/controller"
	ujresource "github.com/crossplane/upjet/v2/pkg/resource"
	"github.com/crossplane/upjet/v2/pkg/terraform"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	clientfake "sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestLiveJellyfinSettings(t *testing.T) {
	path := os.Getenv("JELLYFIN_TEST_CONFIG")
	if path == "" {
		t.Skip("requires an isolated Jellyfin fixture")
	}
	var desired struct {
		BaseURL       string         `json:"baseURL"`
		AdminUsername string         `json:"adminUsername"`
		LDAP          map[string]any `json:"ldapConfiguration"`
		Libraries     []struct {
			Name, CollectionType string
			Paths                []string
		} `json:"libraries"`
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &desired); err != nil {
		t.Fatal(err)
	}
	credentials := os.Getenv("JELLYFIN_TEST_CREDENTIALS")
	password, err := os.ReadFile(filepath.Join(credentials, "JELLYFIN_ADMIN_PASSWORD"))
	if err != nil {
		t.Fatal(err)
	}
	bindPassword, err := os.ReadFile(filepath.Join(credentials, "LDAP_BIND_PASSWORD"))
	if err != nil {
		t.Fatal(err)
	}
	desired.LDAP["LdapBindPassword"] = string(bindPassword)
	configuration, err := json.Marshal(desired.LDAP)
	if err != nil {
		t.Fatal(err)
	}
	scheme := runtime.NewScheme()
	_ = corev1.AddToScheme(scheme)
	secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "settings", Namespace: "media"}, Data: map[string][]byte{"json": configuration}}
	kube := clientfake.NewClientBuilder().WithScheme(scheme).WithObjects(secret).Build()
	setup := func(context.Context, client.Client, xpresource.Managed) (terraform.Setup, error) {
		return terraform.Setup{FrameworkProvider: localconfig.FrameworkProvider(), Configuration: terraform.ProviderConfiguration{"endpoint": desired.BaseURL, "username": desired.AdminUsername, "password": string(password)}}, nil
	}
	provider := localconfig.Namespaced()
	store := controller.NewOperationStore(logging.NewNopLogger())
	reconcile := func(name string, obj ujresource.Terraformed) {
		t.Helper()
		obj.SetName(name + "-" + strings.ToLower(meta.GetExternalName(obj)))
		obj.SetNamespace("media")
		obj.SetUID(types.UID(obj.GetName()))
		connector := controller.NewTerraformPluginFrameworkConnector(kube, setup, provider.Resources[name], store, controller.WithTerraformPluginFrameworkLogger(logging.NewNopLogger()))
		external, err := connector.Connect(t.Context(), obj)
		if err != nil {
			t.Fatal(err)
		}
		observed, err := external.Observe(t.Context(), obj)
		if err != nil {
			t.Fatal(err)
		}
		if os.Getenv("JELLYFIN_TEST_CHECK_ONLY") == "true" {
			if !observed.ResourceExists || !observed.ResourceUpToDate {
				t.Fatalf("%s is not synchronized", name)
			}
			return
		}
		if !observed.ResourceExists {
			if _, err = external.Create(t.Context(), obj); err != nil {
				t.Fatal(err)
			}
		} else if !observed.ResourceUpToDate {
			if _, err = external.Update(t.Context(), obj); err != nil {
				t.Fatal(err)
			}
		}
	}
	plugin := &api.PluginConfiguration{}
	meta.SetExternalName(plugin, "958aad66-3784-4d2a-b89a-a7b6fab6e25c")
	plugin.Spec.ForProvider.ConfigurationJSONSecretRef = &xpv2.LocalSecretKeySelector{LocalSecretReference: xpv2.LocalSecretReference{Name: "settings"}, Key: "json"}
	reconcile("jellyfin_plugin_configuration", plugin)
	serverName, culture, country, language := "Jellyfin", "en-US", "US", "en"
	system := &api.SystemConfiguration{}
	meta.SetExternalName(system, "system")
	system.Spec.ForProvider.ServerName = &serverName
	system.Spec.ForProvider.UICulture = &culture
	system.Spec.ForProvider.MetadataCountryCode = &country
	system.Spec.ForProvider.PreferredMetadataLanguage = &language
	reconcile("jellyfin_system_configuration", system)
	upnp, remote := false, true
	network := &api.NetworkingConfiguration{}
	meta.SetExternalName(network, "network")
	network.Spec.ForProvider.EnableUpnp = &upnp
	network.Spec.ForProvider.EnableRemoteAccess = &remote
	reconcile("jellyfin_networking_configuration", network)
	disclaimer := ""
	branding := &api.BrandingConfiguration{}
	meta.SetExternalName(branding, "branding")
	branding.Spec.ForProvider.LoginDisclaimer = &disclaimer
	reconcile("jellyfin_branding_configuration", branding)
	for _, library := range desired.Libraries {
		obj := &api.Library{}
		meta.SetExternalName(obj, library.Name)
		obj.Spec.ForProvider.CollectionType = &library.CollectionType
		for _, path := range library.Paths {
			obj.Spec.ForProvider.Paths = append(obj.Spec.ForProvider.Paths, &path)
		}
		reconcile("jellyfin_library", obj)
	}
}
