//go:build system

// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package integration

import (
	"bytes"
	"context"
	"fmt"
	"image"
	"image/jpeg"
	"os"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/go-ldap/ldap/v3"
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

func TestFoundationAdoptionPreservesRestoredIdentity(t *testing.T) {
	ns, ip := os.Getenv("POD_NAMESPACE"), os.Getenv("TEST_POD_IP")
	if ns == "" || ip == "" {
		t.Skip("requires the Helm test Pod's in-cluster environment")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
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
	f := startLDAP(t)
	name := fmt.Sprintf("foundation-%x", time.Now().UnixNano())
	pcGVR := schema.GroupVersionResource{Group: "ldap.ops.finnes.dev", Version: "v1beta1", Resource: "providerconfigs"}
	entryGVR := schema.GroupVersionResource{Group: "ldap.ops.finnes.dev", Version: "v1alpha1", Resource: "entries"}
	entries := dyn.Resource(entryGVR).Namespace(ns)
	createSecret := func(secretName string, data map[string][]byte) {
		t.Helper()
		if _, err := kube.CoreV1().Secrets(ns).Create(ctx, &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: secretName}, Data: data}, metav1.CreateOptions{}); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = kube.CoreV1().Secrets(ns).Delete(context.Background(), secretName, metav1.DeleteOptions{}) })
	}
	createSecret(name, map[string][]byte{"password": []byte(fixturePassword)})
	pc := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "ldap.ops.finnes.dev/v1beta1", "kind": "ProviderConfig", "metadata": map[string]any{"name": name, "namespace": ns}, "spec": map[string]any{"host": ip, "port": int64(f.port), "bindDN": "cn=admin," + baseDN, "tls": false, "credentialsSecretRef": map[string]any{"name": name, "key": "password"}}}}
	if _, err = dyn.Resource(pcGVR).Namespace(ns).Create(ctx, pc, metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = dyn.Resource(pcGVR).Namespace(ns).Delete(context.Background(), name, metav1.DeleteOptions{})
	})
	var photo bytes.Buffer
	if err := jpeg.Encode(&photo, image.NewRGBA(image.Rect(0, 0, 2, 2)), nil); err != nil {
		t.Fatal(err)
	}
	adminDN, humanDN, groupDN := "uid=admin,ou=users,"+baseDN, "uid=operator,ou=users,"+baseDN, "cn=admin,ou=groups,"+baseDN
	for _, dn := range []string{adminDN, humanDN} {
		add(t, f.conn, dn, map[string][]string{"objectClass": {"inetOrgPerson"}, "cn": {"Changed profile"}, "sn": {"Human"}, "mail": {"changed@example.test"}, "userPassword": {"current-human-password"}, "jpegPhoto": {photo.String()}})
	}
	add(t, f.conn, groupDN, map[string][]string{"objectClass": {"groupOfNames"}, "member": {humanDN}, "description": {"existing description"}})
	snapshot := func(dn string) map[string][]string {
		t.Helper()
		r, err := f.conn.Search(ldap.NewSearchRequest(dn, ldap.ScopeBaseObject, ldap.NeverDerefAliases, 1, 0, false, "(objectClass=*)", []string{"entryUUID", "entryCSN", "cn", "sn", "mail", "userPassword", "jpegPhoto", "member"}, nil))
		if err != nil || len(r.Entries) != 1 {
			t.Fatal("cannot read synthetic identity snapshot")
		}
		if r.Entries[0].GetAttributeValue("entryUUID") == "" {
			t.Fatal("synthetic identity has no persistent UUID")
		}
		result := map[string][]string{}
		for _, a := range r.Entries[0].Attributes {
			result[a.Name] = a.Values
		}
		return result
	}
	dns := []string{adminDN, humanDN, groupDN}
	original := map[string]map[string][]string{}
	documents := map[string][]byte{}
	for i, dn := range dns {
		original[dn] = snapshot(dn)
		attrs := map[string][]string{"objectClass": {"inetOrgPerson", "extensibleObject"}}
		defaults := map[string][]string{"cn": {"Stale bootstrap"}, "sn": {"Bootstrap"}, "mail": {"stale@example.test"}, "userPassword": {"stale-bootstrap-password"}, "jpegPhoto": {"stale-bootstrap-photo"}}
		if dn == groupDN {
			attrs = map[string][]string{"objectClass": {"groupOfNames"}, "description": {"Declared administrators"}}
			defaults = map[string][]string{"member": {"cn=admin," + baseDN}}
		}
		documents[fmt.Sprintf("%d.json", i)] = []byte(encoded(t, attrs))
		documents[fmt.Sprintf("%d-defaults.json", i)] = []byte(encoded(t, defaults))
	}
	createSecret(name+"-attributes", documents)
	poll := func(description string, condition func(context.Context) (bool, error)) {
		t.Helper()
		if err := wait.PollUntilContextTimeout(ctx, 500*time.Millisecond, 45*time.Second, true, condition); err != nil {
			t.Fatalf("%s: %v", description, err)
		}
	}
	remove := func(entryName string) {
		t.Helper()
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if err := entries.Delete(cleanupCtx, entryName, metav1.DeleteOptions{}); err != nil && !apierrors.IsNotFound(err) {
			t.Error(err)
			return
		}
		if err := wait.PollUntilContextTimeout(cleanupCtx, 500*time.Millisecond, 30*time.Second, true, func(ctx context.Context) (bool, error) {
			_, err := entries.Get(ctx, entryName, metav1.GetOptions{})
			return apierrors.IsNotFound(err), nil
		}); err != nil {
			t.Error("management resource did not finalize")
		}
	}
	templates := make([]*unstructured.Unstructured, len(dns))
	for i, dn := range dns {
		entryName := fmt.Sprintf("%s-%d", name, i)
		owned := []any{"objectClass"}
		if dn == groupDN {
			owned = append(owned, "description")
		}
		templates[i] = &unstructured.Unstructured{Object: map[string]any{"apiVersion": "ldap.ops.finnes.dev/v1alpha1", "kind": "Entry", "metadata": map[string]any{"name": entryName, "namespace": ns, "annotations": map[string]any{"crossplane.io/external-name": dn}}, "spec": map[string]any{"managementPolicies": []any{"Observe"}, "providerConfigRef": map[string]any{"kind": "ProviderConfig", "name": name}, "forProvider": map[string]any{"dataJsonSecretRef": map[string]any{"name": name + "-attributes", "key": fmt.Sprintf("%d.json", i)}, "dataJsonCreateDefaultsSecretRef": map[string]any{"name": name + "-attributes", "key": fmt.Sprintf("%d-defaults.json", i)}, "restrictAttributes": owned}}}}
		if _, err := entries.Create(ctx, templates[i], metav1.CreateOptions{}); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { remove(entryName) })
		poll("observe-only adoption", func(ctx context.Context) (bool, error) {
			obj, err := entries.Get(ctx, entryName, metav1.GetOptions{})
			if err != nil {
				return false, err
			}
			conditions, _, _ := unstructured.NestedSlice(obj.Object, "status", "conditions")
			for _, raw := range conditions {
				c := raw.(map[string]any)
				if c["type"] == "Synced" && c["status"] == "True" {
					return true, nil
				}
			}
			return false, nil
		})
	}
	for _, dn := range dns {
		if !reflect.DeepEqual(snapshot(dn), original[dn]) {
			t.Fatal("observe-only adoption wrote to a restored identity")
		}
	}
	for _, template := range templates {
		current, err := entries.Get(ctx, template.GetName(), metav1.GetOptions{})
		if err != nil {
			t.Fatal(err)
		}
		if err := unstructured.SetNestedSlice(current.Object, []any{"Observe", "Create", "Update"}, "spec", "managementPolicies"); err != nil {
			t.Fatal(err)
		}
		if _, err := entries.Update(ctx, current, metav1.UpdateOptions{}); err != nil {
			t.Fatal(err)
		}
	}
	poll("owned attributes reconciled", func(context.Context) (bool, error) {
		for _, dn := range []string{adminDN, humanDN} {
			if !slices.ContainsFunc(read(t, f.conn, dn).GetAttributeValues("objectClass"), func(value string) bool { return strings.EqualFold(value, "extensibleObject") }) {
				return false, nil
			}
		}
		return read(t, f.conn, groupDN).GetAttributeValue("description") == "Declared administrators", nil
	})
	for _, dn := range dns {
		before, after := original[dn], snapshot(dn)
		delete(before, "entryCSN")
		delete(after, "entryCSN")
		if !reflect.DeepEqual(before, after) {
			t.Fatal("managed adoption changed passwords, profile, membership, photo or entryUUID")
		}
	}
	for _, template := range templates {
		dn := template.GetAnnotations()["crossplane.io/external-name"]
		remove(template.GetName())
		if !reflect.DeepEqual(original[dn], func() map[string][]string { v := snapshot(dn); delete(v, "entryCSN"); return v }()) {
			t.Fatal("orphaning management resource changed directory identity")
		}
		_ = unstructured.SetNestedSlice(template.Object, []any{"Observe", "Create", "Update"}, "spec", "managementPolicies")
		if _, err := entries.Create(ctx, template, metav1.CreateOptions{}); err != nil {
			t.Fatal(err)
		}
		poll("state-free re-adoption", func(ctx context.Context) (bool, error) {
			obj, err := entries.Get(ctx, template.GetName(), metav1.GetOptions{})
			if err != nil {
				return false, err
			}
			conditions, _, _ := unstructured.NestedSlice(obj.Object, "status", "conditions")
			for _, raw := range conditions {
				c := raw.(map[string]any)
				if c["type"] == "Synced" && c["status"] == "True" {
					return true, nil
				}
			}
			return false, nil
		})
		after := snapshot(dn)
		delete(after, "entryCSN")
		if !reflect.DeepEqual(original[dn], after) {
			t.Fatal("re-adoption recreated or changed the retained identity")
		}
	}
	t.Log("restored legacy identities survive observe/manage adoption, orphaning and state-free re-adoption with stable UUIDs")
}
