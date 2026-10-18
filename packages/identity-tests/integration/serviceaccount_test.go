//go:build system

// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package integration

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/go-ldap/ldap/v3"
	"golang.org/x/crypto/bcrypt"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/wait"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

func TestServiceAccountComposition(t *testing.T) {
	if os.Getenv("GROUPS_ENABLED") != "true" || os.Getenv("POD_NAMESPACE") == "" {
		t.Skip("requires identity compositions in the disposable fixture")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
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
	ns := os.Getenv("POD_NAMESPACE")
	name := fmt.Sprintf("service-%x", time.Now().UnixNano())
	f := startLDAP(t)
	if _, err := kube.CoreV1().Secrets(ns).Create(ctx, &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: name}, Data: connectionSecretData(os.Getenv("TEST_POD_IP"), f.port)}, metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = kube.CoreV1().Secrets(ns).Delete(context.Background(), name, metav1.DeleteOptions{}) })
	providers := dyn.Resource(schema.GroupVersionResource{Group: "ldap.finnes.dev", Version: "v1beta1", Resource: "providerconfigs"}).Namespace(ns)
	pc := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "ldap.finnes.dev/v1beta1", "kind": "ProviderConfig", "metadata": map[string]any{"name": name, "namespace": ns}, "spec": providerConnectionSpec(name, ns)}}
	if _, err := providers.Create(ctx, pc, metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = providers.Delete(context.Background(), name, metav1.DeleteOptions{}) })
	accounts := dyn.Resource(schema.GroupVersionResource{Group: "identity.ops.finnes.dev", Version: "v1alpha1", Resource: "serviceaccounts"}).Namespace(ns)
	xr := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "identity.ops.finnes.dev/v1alpha1", "kind": "ServiceAccount", "metadata": map[string]any{"name": name, "namespace": ns}, "spec": map[string]any{"cn": "service.original", "passwordPolicyDN": "", "baseDN": "ou=users," + baseDN, "backendRef": map[string]any{"kind": "ProviderConfig", "name": name}}}}
	if _, err := accounts.Create(ctx, xr, metav1.CreateOptions{FieldValidation: "Strict"}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = accounts.Delete(context.Background(), name, metav1.DeleteOptions{}) })
	var lastConditions []any
	confirmed := func(rotation string) *corev1.Secret {
		t.Helper()
		var secret *corev1.Secret
		err := wait.PollUntilContextTimeout(ctx, 500*time.Millisecond, 50*time.Second, true, func(context.Context) (bool, error) {
			current, err := accounts.Get(ctx, name, metav1.GetOptions{})
			if err != nil {
				return false, err
			}
			lastConditions, _, _ = unstructured.NestedSlice(current.Object, "status", "conditions")
			ready, synced := false, false
			for _, raw := range lastConditions {
				c := raw.(map[string]any)
				if c["status"] == "True" {
					ready = ready || c["type"] == "Ready"
					synced = synced || c["type"] == "Synced"
				}
			}
			secretName, _, _ := unstructured.NestedString(current.Object, "status", "secretRef", "name")
			version, _, _ := unstructured.NestedString(current.Object, "status", "secretRef", "resourceVersion")
			if !ready || !synced || secretName == "" || version == "" {
				return false, nil
			}
			secret, err = kube.CoreV1().Secrets(ns).Get(ctx, secretName, metav1.GetOptions{})
			if err != nil {
				return false, err
			}
			return secret.ResourceVersion == version && secret.Annotations["identity.ops.finnes.dev/rotation-id"] == rotation, nil
		})
		if err != nil {
			t.Fatalf("service-account confirmed credentials: %v; conditions=%v", err, lastConditions)
		}
		return secret
	}
	verify := func(secret *corev1.Secret, cn string) string {
		t.Helper()
		password := secret.Data["password"]
		hash := secret.Data["ldap-password"]
		if len(password) != 32 || !strings.HasPrefix(string(hash), "{CRYPT}") || bcrypt.CompareHashAndPassword([]byte(strings.TrimPrefix(string(hash), "{CRYPT}")), password) != nil {
			t.Fatal("generated credentials lack the required 32-character password and matching bcrypt hash")
		}
		dn := "uid=" + cn + ",ou=users," + baseDN
		client, err := ldap.DialURL(fmt.Sprintf("ldap://127.0.0.1:%d", f.port))
		if err != nil {
			t.Fatal(err)
		}
		defer client.Close()
		if err := client.Bind(dn, string(password)); err != nil {
			t.Fatal("generated service credentials cannot authenticate")
		}
		result, err := f.conn.Search(ldap.NewSearchRequest(dn, ldap.ScopeBaseObject, ldap.NeverDerefAliases, 1, 0, false, "(objectClass=*)", []string{"entryUUID"}, nil))
		if err != nil || len(result.Entries) != 1 {
			t.Fatal("service-account LDAP identity missing")
		}
		uuid := result.Entries[0].GetAttributeValue("entryUUID")
		if uuid == "" {
			t.Fatal("service-account UUID missing")
		}
		return uuid
	}
	original := confirmed("")
	uuid := verify(original, "service.original")
	update := func(key, value string) {
		t.Helper()
		current, err := accounts.Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			t.Fatal(err)
		}
		_ = unstructured.SetNestedField(current.Object, value, "spec", key)
		if _, err := accounts.Update(ctx, current, metav1.UpdateOptions{}); err != nil {
			t.Fatal(err)
		}
	}
	update("cn", "service.renamed")
	// A previous Ready status can remain briefly after an XR spec update; verify
	// the external rename before accepting the retained credential reference.
	if err := wait.PollUntilContextTimeout(ctx, 500*time.Millisecond, 50*time.Second, true, func(context.Context) (bool, error) {
		result, err := f.conn.Search(ldap.NewSearchRequest("uid=service.renamed,ou=users,"+baseDN, ldap.ScopeBaseObject, ldap.NeverDerefAliases, 1, 0, false, "(objectClass=*)", []string{"entryUUID"}, nil))
		if ldap.IsErrorWithCode(err, ldap.LDAPResultNoSuchObject) {
			return false, nil
		}
		if err != nil {
			return false, err
		}
		return len(result.Entries) == 1 && result.Entries[0].GetAttributeValue("entryUUID") == uuid, nil
	}); err != nil {
		t.Fatalf("service-account rename: %v", err)
	}
	renamed := confirmed("")
	if renamed.UID != original.UID || string(renamed.Data["password"]) != string(original.Data["password"]) || verify(renamed, "service.renamed") != uuid {
		t.Fatal("rename replaced credentials or LDAP identity")
	}
	update("rotationID", "rotate-once")
	rotated := confirmed("rotate-once")
	if rotated.UID != original.UID || string(rotated.Data["password"]) == string(original.Data["password"]) || verify(rotated, "service.renamed") != uuid {
		t.Fatal("rotation did not change the password while preserving identity")
	}
	oldClient, err := ldap.DialURL(fmt.Sprintf("ldap://127.0.0.1:%d", f.port))
	if err != nil {
		t.Fatal(err)
	}
	defer oldClient.Close()
	if err := oldClient.Bind("uid=service.renamed,ou=users,"+baseDN, string(original.Data["password"])); !ldap.IsErrorWithCode(err, ldap.LDAPResultInvalidCredentials) {
		t.Fatal("rotation did not revoke the old password")
	}
	t.Log("ServiceAccount generates durable credentials, confirms LDAP convergence, preserves identity on rename, and rotates passwords")
}
