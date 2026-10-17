//go:build system

// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package integration

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/wait"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

func TestHelmReadiness(t *testing.T) {
	ns := os.Getenv("POD_NAMESPACE")
	if ns == "" {
		t.Skip("requires the Helm test Pod")
	}
	cfg, err := rest.InClusterConfig()
	if err != nil {
		t.Fatal(err)
	}
	kube, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	deployment, err := kube.AppsV1().Deployments(ns).Get(ctx, os.Getenv("HELM_RELEASE"), metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if deployment.Status.ObservedGeneration < deployment.Generation || deployment.Status.AvailableReplicas < 1 {
		t.Fatal("provider Deployment is not available at its current generation")
	}
	groups := []string{"ldap.ops.finnes.dev/v1alpha1", "ldap.ops.finnes.dev/v1beta1"}
	if os.Getenv("OAUTH2_ENABLED") == "true" {
		groups = append(groups, "identity.ops.finnes.dev/v1alpha1")
	}
	for _, group := range groups {
		if _, err := kube.Discovery().ServerResourcesForGroupVersion(group); err != nil {
			t.Fatal(err)
		}
	}
	t.Log("provider is available and installed APIs are discoverable; no directory or workload changes were made")
}

func TestOAuth2ClientComposition(t *testing.T) {
	if os.Getenv("OAUTH2_ENABLED") != "true" {
		t.Skip("enable oauth2 for the Crossplane composition test")
	}
	ns := os.Getenv("POD_NAMESPACE")
	if ns == "" {
		t.Skip("requires the Helm test Pod")
	}
	cfg, err := rest.InClusterConfig()
	if err != nil {
		t.Fatal(err)
	}
	kube, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		t.Fatal(err)
	}
	dyn, err := dynamic.NewForConfig(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	defer cancel()
	f := startLDAP(t)
	suffix := fmt.Sprintf("%x", time.Now().UnixNano())
	pcName := "composed-" + suffix
	credentialsName := pcName + "-credentials"
	pcGVR := schema.GroupVersionResource{Group: "ldap.ops.finnes.dev", Version: "v1beta1", Resource: "providerconfigs"}
	entryGVR := schema.GroupVersionResource{Group: "ldap.ops.finnes.dev", Version: "v1alpha1", Resource: "entries"}
	xrGVR := schema.GroupVersionResource{Group: "identity.ops.finnes.dev", Version: "v1alpha1", Resource: "oauth2clients"}
	xrs := dyn.Resource(xrGVR).Namespace(ns)
	putSecret := func(name, key, value string) {
		t.Helper()
		_, err := kube.CoreV1().Secrets(ns).Create(ctx, &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: name}, Data: map[string][]byte{key: []byte(value)}}, metav1.CreateOptions{})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = kube.CoreV1().Secrets(ns).Delete(context.Background(), name, metav1.DeleteOptions{}) })
	}
	putSecret(pcName, "password", fixturePassword)
	pc := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "ldap.ops.finnes.dev/v1beta1", "kind": "ProviderConfig", "metadata": map[string]any{"name": pcName, "namespace": ns}, "spec": map[string]any{"host": os.Getenv("TEST_POD_IP"), "port": int64(f.port), "bindDN": "cn=admin," + baseDN, "tls": false, "credentialsSecretRef": map[string]any{"name": pcName, "key": "password"}}}}
	if _, err := dyn.Resource(pcGVR).Namespace(ns).Create(ctx, pc, metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = dyn.Resource(pcGVR).Namespace(ns).Delete(context.Background(), pcName, metav1.DeleteOptions{})
	})
	firstSecret := "fixture-quoted-secret-1\"\\end"
	putSecret(credentialsName, "client-secret", firstSecret)
	makeXR := func(name, id, credential string, public bool) *unstructured.Unstructured {
		spec := map[string]any{"backendRef": map[string]any{"name": pcName, "kind": "ProviderConfig"}, "clientID": id, "displayName": "Immich", "redirectURIs": []any{"https://immich.example.test/auth/login"}}
		if public {
			spec["tokenEndpointAuthMethod"] = "none"
		} else {
			spec["clientSecretRef"] = map[string]any{"name": credential, "key": "client-secret"}
		}
		return &unstructured.Unstructured{Object: map[string]any{"apiVersion": "identity.ops.finnes.dev/v1alpha1", "kind": "OAuth2Client", "metadata": map[string]any{"name": name, "namespace": ns}, "spec": spec}}
	}
	createXR := func(xr *unstructured.Unstructured) *unstructured.Unstructured {
		t.Helper()
		created, err := xrs.Create(ctx, xr, metav1.CreateOptions{FieldValidation: "Strict"})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = xrs.Delete(context.Background(), created.GetName(), metav1.DeleteOptions{}) })
		return created
	}
	waitFor := func(label string, check func() (bool, error)) {
		t.Helper()
		err := wait.PollUntilContextTimeout(ctx, 500*time.Millisecond, 60*time.Second, true, func(context.Context) (bool, error) { return check() })
		if err != nil {
			t.Fatalf("%s: %v", label, err)
		}
	}
	hasCondition := func(obj *unstructured.Unstructured, kind, value string) bool {
		conditions, _, _ := unstructured.NestedSlice(obj.Object, "status", "conditions")
		for _, raw := range conditions {
			c := raw.(map[string]any)
			if c["type"] == kind && c["status"] == value {
				return true
			}
		}
		return false
	}
	waitReady := func(name string) {
		t.Helper()
		waitFor("OAuth2Client "+name+" Ready", func() (bool, error) {
			xr, err := xrs.Get(ctx, name, metav1.GetOptions{})
			if err != nil {
				return false, err
			}
			return hasCondition(xr, "Ready", "True") && hasCondition(xr, "Synced", "True"), nil
		})
	}
	waitValue := func(dn, attr, want string) {
		t.Helper()
		waitFor("LDAP "+attr+" convergence", func() (bool, error) {
			result, err := f.conn.Search(ldapSearch(dn))
			if err != nil {
				return false, nil
			}
			return len(result.Entries) == 1 && result.Entries[0].GetAttributeValue(attr) == want, nil
		})
	}
	desired := makeXR(pcName, "immich", credentialsName, false)
	xr := createXR(desired)
	name := xr.GetName()
	dn := "oauthClientID=immich,ou=clients,ou=oauth," + baseDN
	waitReady(name)
	waitValue(dn, "oauthClientSecret", firstSecret)
	if read(t, f.conn, dn).GetAttributeValue("oauthScope") == "" {
		t.Fatal("default OIDC scopes missing")
	}
	selector := "identity.ops.finnes.dev/client-uid=" + string(xr.GetUID())
	entries, err := dyn.Resource(entryGVR).Namespace(ns).List(ctx, metav1.ListOptions{LabelSelector: selector})
	if err != nil || len(entries.Items) != 1 {
		t.Fatalf("expected one composed LDAP Entry: %v", err)
	}
	documents, err := kube.CoreV1().Secrets(ns).List(ctx, metav1.ListOptions{LabelSelector: selector})
	if err != nil || len(documents.Items) != 1 {
		t.Fatalf("expected one composed attributes Secret: %v", err)
	}
	for _, owner := range []metav1.Object{&entries.Items[0], &documents.Items[0]} {
		refs := owner.GetOwnerReferences()
		if len(refs) != 1 || refs[0].UID != xr.GetUID() {
			t.Fatal("composed resources are not owned by this namespaced client")
		}
	}
	t.Log("application-facing OAuth2Client composed a local Secret and LDAP Entry; JSON quoting and default OIDC fields work")
	invalid := desired.DeepCopy()
	invalid.SetName(pcName + "-invalid")
	_ = unstructured.SetNestedField(invalid.Object, "unsafe,id", "spec", "clientID")
	if _, err := xrs.Create(ctx, invalid, metav1.CreateOptions{}); !apierrors.IsInvalid(err) {
		t.Fatalf("unsafe client ID accepted: %v", err)
	}
	invalid = makeXR(pcName+"-invalid", "public-invalid", credentialsName, true)
	_ = unstructured.SetNestedMap(invalid.Object, map[string]any{"name": credentialsName, "key": "client-secret"}, "spec", "clientSecretRef")
	if _, err := xrs.Create(ctx, invalid, metav1.CreateOptions{}); !apierrors.IsInvalid(err) {
		t.Fatalf("public client with a secret accepted: %v", err)
	}
	invalid = desired.DeepCopy()
	invalid.SetName(pcName + "-invalid")
	_ = unstructured.SetNestedField(invalid.Object, "another-namespace", "spec", "clientSecretRef", "namespace")
	if _, err := xrs.Create(ctx, invalid, metav1.CreateOptions{FieldValidation: "Strict"}); !apierrors.IsInvalid(err) && !apierrors.IsBadRequest(err) {
		t.Fatalf("cross-namespace credential field accepted: %v", err)
	}
	current, err := xrs.Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	_ = unstructured.SetNestedField(current.Object, "different-id", "spec", "clientID")
	if _, err := xrs.Update(ctx, current, metav1.UpdateOptions{}); !apierrors.IsInvalid(err) {
		t.Fatalf("immutable ID change accepted: %v", err)
	}
	t.Log("API rejected LDAP DN injection, invalid public credentials, cross-namespace references, and client-ID changes")
	modify(t, f.conn, dn, map[string][]string{"oauthClientName": {"wrong"}, "oauthIssueDate": {"20261018000000Z"}, "oauthPreconsent": {"TRUE"}})
	waitValue(dn, "oauthClientName", "Immich")
	if read(t, f.conn, dn).GetAttributeValue("oauthIssueDate") != "20261018000000Z" || read(t, f.conn, dn).GetAttributeValue("oauthPreconsent") != "TRUE" {
		t.Fatal("runtime metadata was overwritten")
	}
	// Repair a composed object as well as the remote LDAP entry.
	document := documents.Items[0].DeepCopy()
	var attrs map[string][]string
	if err := json.Unmarshal(document.Data["attributes.json"], &attrs); err != nil {
		t.Fatal(err)
	}
	attrs["oauthClientName"] = []string{"wrong-composed-value"}
	document.Data["attributes.json"] = []byte(encoded(t, attrs))
	if _, err := kube.CoreV1().Secrets(ns).Update(ctx, document, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	waitFor("composed Secret drift repair", func() (bool, error) {
		doc, err := kube.CoreV1().Secrets(ns).Get(ctx, document.Name, metav1.GetOptions{})
		if err != nil {
			return false, err
		}
		var a map[string][]string
		if err := json.Unmarshal(doc.Data["attributes.json"], &a); err != nil {
			return false, err
		}
		return len(a["oauthClientName"]) == 1 && a["oauthClientName"][0] == "Immich", nil
	})
	waitValue(dn, "oauthClientName", "Immich")
	credential, err := kube.CoreV1().Secrets(ns).Get(ctx, credentialsName, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	credential.Data["client-secret"] = []byte("fixture-client-secret-2")
	if _, err := kube.CoreV1().Secrets(ns).Update(ctx, credential, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	waitValue(dn, "oauthClientSecret", "fixture-client-secret-2")
	current, err = xrs.Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	encodedXR, _ := json.Marshal(current.Object)
	for _, s := range []string{firstSecret, "fixture-client-secret-2", base64.StdEncoding.EncodeToString([]byte("fixture-client-secret-2"))} {
		if strings.Contains(string(encodedXR), s) {
			t.Fatal("client credential leaked into the application CR")
		}
	}
	verify := exec.Command(os.Getenv("CANAILLE_TEST_PYTHON"), "../tests/verify_canaille.py")
	verify.Env = append(os.Environ(), "LDAP_TEST_URI=ldap://127.0.0.1:"+strconv.Itoa(f.port))
	if out, err := verify.CombinedOutput(); err != nil {
		t.Fatalf("Canaille authentication of composed client: %v: %s", err, out)
	}
	t.Log("Crossplane repaired composed-object drift; local Secret rotation reached LDAP and Canaille without changing the client CR")
	current, err = xrs.Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	_ = unstructured.SetNestedSlice(current.Object, []any{"https://immich.example.test/logout"}, "spec", "postLogoutRedirectURIs")
	if _, err := xrs.Update(ctx, current, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	waitValue(dn, "oauthPostLogoutRedirectURI", "https://immich.example.test/logout")
	current, err = xrs.Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	unstructured.RemoveNestedField(current.Object, "spec", "postLogoutRedirectURIs")
	if _, err := xrs.Update(ctx, current, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	waitValue(dn, "oauthPostLogoutRedirectURI", "")
	public := createXR(makeXR(pcName+"-public", "public-composed", "", true))
	waitReady(public.GetName())
	if read(t, f.conn, "oauthClientID=public-composed,ou=clients,ou=oauth,"+baseDN).GetAttributeValue("oauthClientSecret") != "" {
		t.Fatal("public client acquired a secret")
	}
	missingName := pcName + "-missing-secret"
	missing := createXR(makeXR(pcName+"-missing", "missing-composed", missingName, false))
	waitFor("missing credential error", func() (bool, error) {
		obj, err := xrs.Get(ctx, missing.GetName(), metav1.GetOptions{})
		if err != nil {
			return false, err
		}
		return hasCondition(obj, "Synced", "False"), nil
	})
	missingDN := "oauthClientID=missing-composed,ou=clients,ou=oauth," + baseDN
	if _, err := f.conn.Search(ldapSearch(missingDN)); err == nil {
		t.Fatal("client was created without its referenced local secret")
	}
	putSecret(missingName, "client-secret", "fixture-recovered-secret")
	waitReady(missing.GetName())
	waitValue(missingDN, "oauthClientSecret", "fixture-recovered-secret")
	t.Log("public clients require no secret; missing credentials fail closed and recover when the local Secret appears")
	if err := xrs.Delete(ctx, name, metav1.DeleteOptions{}); err != nil {
		t.Fatal(err)
	}
	waitFor("client deletion", func() (bool, error) {
		_, err := xrs.Get(ctx, name, metav1.GetOptions{})
		return apierrors.IsNotFound(err), nil
	})
	waitFor("composed resources cleanup", func() (bool, error) {
		list, err := dyn.Resource(entryGVR).Namespace(ns).List(ctx, metav1.ListOptions{LabelSelector: selector})
		if err != nil {
			return false, err
		}
		secrets, err := kube.CoreV1().Secrets(ns).List(ctx, metav1.ListOptions{LabelSelector: selector})
		return len(list.Items) == 0 && len(secrets.Items) == 0, err
	})
	if read(t, f.conn, dn).GetAttributeValue("oauthClientSecret") != "fixture-client-secret-2" {
		t.Fatal("deleting the client lost the external identity")
	}
	adopted := createXR(desired)
	waitReady(adopted.GetName())
	if read(t, f.conn, dn).GetAttributeValue("oauthIssueDate") != "20261018000000Z" {
		t.Fatal("re-adoption reset runtime metadata")
	}
	t.Log("deleting the client cleaned composed resources, retained LDAP data, and a fresh CR adopted it without tfstate")
}
