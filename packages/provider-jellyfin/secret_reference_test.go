// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package crossplane

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	api "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/apis/namespaced/jellyfin/v1alpha1"
	localconfig "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/config"
	"github.com/crossplane/crossplane-runtime/v2/pkg/logging"
	"github.com/crossplane/crossplane-runtime/v2/pkg/meta"
	xpresource "github.com/crossplane/crossplane-runtime/v2/pkg/resource"
	xpv2 "github.com/crossplane/crossplane/apis/v2/core/v2"
	"github.com/crossplane/upjet/v2/pkg/controller"
	"github.com/crossplane/upjet/v2/pkg/terraform"
	corev1 "k8s.io/api/core/v1"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

func TestPluginSecretReferenceWithAPIClient(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("observation wrote to %s", r.URL.Path)
			w.WriteHeader(http.StatusForbidden)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/namespaces/media/secrets/settings":
			_ = json.NewEncoder(w).Encode(corev1.Secret{
				TypeMeta:   metav1.TypeMeta{APIVersion: "v1", Kind: "Secret"},
				ObjectMeta: metav1.ObjectMeta{Name: "settings", Namespace: "media"},
				Data:       map[string][]byte{"json": []byte(`{"owned":2}`)},
			})
		case "/System/Info/Public":
			_, _ = io.WriteString(w, `{"StartupWizardCompleted":true,"Version":"12.2"}`)
		case "/Plugins/plugin/Configuration":
			_, _ = io.WriteString(w, `{"owned":1,"runtime":true}`)
		default:
			t.Errorf("unexpected request %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(server.Close)
	scheme := runtime.NewScheme()
	_ = corev1.AddToScheme(scheme)
	mapper := apimeta.NewDefaultRESTMapper([]schema.GroupVersion{corev1.SchemeGroupVersion})
	mapper.Add(corev1.SchemeGroupVersion.WithKind("Secret"), apimeta.RESTScopeNamespace)
	kube, err := client.New(&rest.Config{Host: server.URL}, client.Options{Scheme: scheme, Mapper: mapper})
	if err != nil {
		t.Fatal(err)
	}
	obj := &api.PluginConfiguration{ObjectMeta: metav1.ObjectMeta{Name: "plugin", Namespace: "media", UID: "plugin"}}
	obj.Spec.ForProvider.ConfigurationJSONSecretRef = &xpv2.LocalSecretKeySelector{
		LocalSecretReference: xpv2.LocalSecretReference{Name: "settings"}, Key: "json",
	}
	meta.SetExternalName(obj, "plugin")
	setup := func(context.Context, client.Client, xpresource.Managed) (terraform.Setup, error) {
		return terraform.Setup{FrameworkProvider: localconfig.FrameworkProvider(), Configuration: terraform.ProviderConfiguration{"endpoint": server.URL, "api_key": "synthetic"}}, nil
	}
	connector := controller.NewTerraformPluginFrameworkConnector(kube, setup,
		localconfig.Namespaced().Resources["jellyfin_plugin_configuration"], controller.NewOperationStore(logging.NewNopLogger()),
		controller.WithTerraformPluginFrameworkLogger(logging.NewNopLogger()))
	external, err := connector.Connect(t.Context(), obj)
	if err != nil {
		t.Fatal(err)
	}
	observed, err := external.Observe(t.Context(), obj)
	if err != nil {
		t.Fatal(err)
	}
	if !observed.ResourceExists || observed.ResourceUpToDate {
		t.Fatalf("expected adopted plugin with drift from the referenced Secret: %+v", observed)
	}
}
