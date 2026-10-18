// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package crossplane

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	api "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/apis/namespaced/jellyfin/v1alpha1"
	localconfig "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/config"
	xpv2 "github.com/crossplane/crossplane/apis/v2/core/v2"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	clientfake "sigs.k8s.io/controller-runtime/pkg/client/fake"

	"github.com/crossplane/crossplane-runtime/v2/pkg/logging"
	"github.com/crossplane/crossplane-runtime/v2/pkg/meta"
	"github.com/crossplane/crossplane-runtime/v2/pkg/reconciler/managed"
	xpresource "github.com/crossplane/crossplane-runtime/v2/pkg/resource"
	"github.com/crossplane/upjet/v2/pkg/config"
	"github.com/crossplane/upjet/v2/pkg/controller"
	"github.com/crossplane/upjet/v2/pkg/terraform"
	"sigs.k8s.io/controller-runtime/pkg/client"

	"github.com/ThePhaseless/terraform-provider-jellyfin/internal/provider"
)

func TestLibraryAdoptionRejectsReplacement(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("adoption or replacement wrote to %s", r.URL.Path)
			w.WriteHeader(http.StatusForbidden)
			return
		}
		switch r.URL.Path {
		case "/System/Info/Public":
			_, _ = io.WriteString(w, `{"StartupWizardCompleted":true,"Version":"12.2"}`)
		case "/Library/VirtualFolders":
			_, _ = io.WriteString(w, `[{"Name":"Movies","ItemId":"existing-library-id","CollectionType":"movies","Locations":["/media/movies"],"LibraryOptions":{}}]`)
		default:
			t.Errorf("unexpected request %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(server.Close)
	obj := &api.Library{ObjectMeta: metav1.ObjectMeta{Name: "movies", Namespace: "media", UID: "movies"}}
	collection, location := "movies", "/media/movies"
	obj.Spec.ForProvider.CollectionType = &collection
	obj.Spec.ForProvider.Paths = []*string{&location}
	meta.SetExternalName(obj, "Movies")
	setup := func(context.Context, client.Client, xpresource.Managed) (terraform.Setup, error) {
		return terraform.Setup{FrameworkProvider: localconfig.FrameworkProvider(), Configuration: terraform.ProviderConfiguration{"endpoint": server.URL, "api_key": "synthetic"}}, nil
	}
	kube := clientfake.NewClientBuilder().Build()
	connect := func() managed.ExternalClient {
		t.Helper()
		connector := controller.NewTerraformPluginFrameworkConnector(kube, setup, localconfig.Namespaced().Resources["jellyfin_library"], controller.NewOperationStore(logging.NewNopLogger()), controller.WithTerraformPluginFrameworkLogger(logging.NewNopLogger()))
		ec, err := connector.Connect(t.Context(), obj)
		if err != nil {
			t.Fatal(err)
		}
		return ec
	}
	for range 2 {
		observation, err := connect().Observe(t.Context(), obj)
		if err != nil {
			t.Fatal(err)
		}
		if !observation.ResourceExists || !observation.ResourceUpToDate {
			t.Fatalf("existing library was not adopted: %+v", observation)
		}
		if obj.Status.AtProvider.ItemID == nil || *obj.Status.AtProvider.ItemID != "existing-library-id" {
			t.Fatal("existing library identity was not preserved")
		}
	}
	location = "/media/different"
	ec := connect()
	if _, err := ec.Observe(t.Context(), obj); err != nil {
		t.Fatal(err)
	}
	if _, err := ec.Update(t.Context(), obj); err == nil || !strings.Contains(err.Error(), "require resource replacement") {
		t.Fatalf("unsafe path update did not fail closed: %v", err)
	}
}

func TestGeneratedResourceReconciliation(t *testing.T) {
	ctx := t.Context()
	var mu sync.Mutex
	remote := `{"section":{"owned":1,"runtime":true}}`
	writes := 0
	setRemote := func(raw string) { mu.Lock(); defer mu.Unlock(); remote = raw }
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/System/Info/Public" {
			_, _ = io.WriteString(w, `{"StartupWizardCompleted":true,"Version":"12.2"}`)
			return
		}
		if r.URL.Path == "/Users/AuthenticateByName" && r.Method == http.MethodPost {
			var credentials struct{ Username, Pw string }
			if err := json.NewDecoder(r.Body).Decode(&credentials); err != nil {
				t.Error(err)
			}
			if credentials.Username != "first-admin" || credentials.Pw != "synthetic-password" {
				t.Error("did not reuse configured administrator")
			}
			_, _ = io.WriteString(w, `{"AccessToken":"synthetic-token"}`)
			return
		}
		if r.URL.Path != "/Plugins/plugin/Configuration" {
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
			return
		}
		mu.Lock()
		defer mu.Unlock()
		switch r.Method {
		case http.MethodGet:
			_, _ = io.WriteString(w, remote)
		case http.MethodPost:
			body, err := io.ReadAll(r.Body)
			if err != nil {
				t.Error(err)
				return
			}
			remote = string(body)
			writes++
			w.WriteHeader(http.StatusNoContent)
		default:
			t.Errorf("unexpected method %s", r.Method)
		}
	}))
	t.Cleanup(server.Close)
	cfg := &config.Resource{
		Name:                             "jellyfin_plugin_configuration",
		TerraformPluginFrameworkResource: provider.NewPluginConfigurationResource(),
		ExternalName:                     config.ParameterAsIdentifier("plugin_id"),
		Sensitive:                        config.Sensitive{AdditionalConnectionDetailsFn: func(map[string]any) (map[string][]byte, error) { return nil, nil }},
	}
	setup := func(context.Context, client.Client, xpresource.Managed) (terraform.Setup, error) {
		return terraform.Setup{
			FrameworkProvider: provider.New("test")(),
			Configuration:     terraform.ProviderConfiguration{"endpoint": server.URL, "username": "first-admin", "password": "synthetic-password"},
		}, nil
	}
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "settings", Namespace: "default"},
		Data:       map[string][]byte{"json": []byte(`{"section":{"owned":2}}`)},
	}
	kube := clientfake.NewClientBuilder().WithScheme(scheme).WithObjects(secret).Build()
	obj := &api.PluginConfiguration{}
	obj.Spec.ForProvider.ConfigurationJSONSecretRef = xpv2.LocalSecretKeySelector{
		LocalSecretReference: xpv2.LocalSecretReference{Name: "settings"}, Key: "json",
	}
	obj.SetNamespace("default")
	obj.SetName("configuration")
	obj.SetUID("configuration")
	meta.SetExternalName(obj, "plugin")
	store := controller.NewOperationStore(logging.NewNopLogger())
	connect := func() managed.ExternalClient {
		t.Helper()
		connector := controller.NewTerraformPluginFrameworkConnector(kube, setup, cfg, store, controller.WithTerraformPluginFrameworkLogger(logging.NewNopLogger()))
		ec, err := connector.Connect(ctx, obj)
		if err != nil {
			t.Fatal(err)
		}
		return ec
	}
	observe := func(upToDate bool) managed.ExternalClient {
		t.Helper()
		ec := connect()
		observation, err := ec.Observe(ctx, obj)
		if err != nil {
			t.Fatal(err)
		}
		if !observation.ResourceExists || observation.ResourceUpToDate != upToDate {
			t.Fatalf("observation = %+v; want exists, upToDate=%t", observation, upToDate)
		}
		return ec
	}
	update := func() {
		t.Helper()
		if _, err := observe(false).Update(ctx, obj); err != nil {
			t.Fatal(err)
		}
		observe(true)
	}
	update()
	setRemote(`{}`)
	observe(false)
	observe(false)
	store = controller.NewOperationStore(logging.NewNopLogger())
	observe(false)
	setRemote(`{"section":{"owned":2,"runtime":"changed"}}`)
	observe(true)
	setRemote(`{"section":false}`)
	observe(false)
	observe(false)
	update()
	setRemote(`{}`)
	secret.Data["json"] = []byte(`{}`)
	if err := kube.Update(ctx, secret); err != nil {
		t.Fatal(err)
	}
	update()
	setRemote(`{"section":{"owned":99,"runtime":"preserve"}}`)
	observe(true)
	store = controller.NewOperationStore(logging.NewNopLogger())
	observe(true)
	if _, err := connect().Delete(ctx, obj); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if writes != 2 || remote != `{"section":{"owned":99,"runtime":"preserve"}}` {
		t.Fatal(fmt.Sprintf("writes=%d, remote=%s", writes, remote))
	}
}
