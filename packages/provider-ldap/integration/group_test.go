//go:build system

// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package integration

import (
	"context"
	"fmt"
	"os"
	"slices"
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

func TestGroupOfNamesComposition(t *testing.T) {
	if os.Getenv("GROUPS_ENABLED") != "true" || os.Getenv("POD_NAMESPACE") == "" {
		t.Skip("requires the group composition in the Helm integration fixture")
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
	name := fmt.Sprintf("group-%x", time.Now().UnixNano())
	pcGVR := schema.GroupVersionResource{Group: "ldap.ops.finnes.dev", Version: "v1beta1", Resource: "providerconfigs"}
	xrGVR := schema.GroupVersionResource{Group: "identity.ops.finnes.dev", Version: "v1alpha1", Resource: "groupofnames"}
	entryGVR := schema.GroupVersionResource{Group: "ldap.ops.finnes.dev", Version: "v1alpha1", Resource: "entries"}
	xrs := dyn.Resource(xrGVR).Namespace(ns)
	f := startLDAP(t)
	secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: name}, Data: map[string][]byte{"password": []byte(fixturePassword)}}
	if _, err := kube.CoreV1().Secrets(ns).Create(ctx, secret, metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = kube.CoreV1().Secrets(ns).Delete(context.Background(), name, metav1.DeleteOptions{}) })
	pc := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "ldap.ops.finnes.dev/v1beta1", "kind": "ProviderConfig", "metadata": map[string]any{"name": name, "namespace": ns}, "spec": map[string]any{"host": os.Getenv("TEST_POD_IP"), "port": int64(f.port), "tls": false, "bindDN": "cn=admin," + baseDN, "credentialsSecretRef": map[string]any{"name": name, "key": "password"}}}}
	if _, err := dyn.Resource(pcGVR).Namespace(ns).Create(ctx, pc, metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = dyn.Resource(pcGVR).Namespace(ns).Delete(context.Background(), name, metav1.DeleteOptions{})
	})
	memberA, memberB := "uid=alice,ou=users,"+baseDN, "uid=bob,ou=users,"+baseDN
	for _, dn := range []string{memberA, memberB} {
		add(t, f.conn, dn, map[string][]string{"objectClass": {"inetOrgPerson"}, "cn": {"Member"}, "sn": {"Fixture"}})
	}
	dn := "cn=adopted-users,ou=groups," + baseDN
	add(t, f.conn, dn, map[string][]string{"objectClass": {"groupOfNames"}, "member": {memberA}, "owner": {memberA}, "description": {"old"}})
	uuidOf := func() string {
		t.Helper()
		result, err := f.conn.Search(ldap.NewSearchRequest(dn, ldap.ScopeBaseObject, ldap.NeverDerefAliases, 1, 0, false, "(objectClass=*)", []string{"entryUUID"}, nil))
		if err != nil || len(result.Entries) != 1 {
			t.Fatal("cannot read group UUID")
		}
		value := result.Entries[0].GetAttributeValue("entryUUID")
		if value == "" {
			t.Fatal("group UUID missing")
		}
		return value
	}
	uuid := uuidOf()
	makeGroup := func(resourceName, cn string) *unstructured.Unstructured {
		return &unstructured.Unstructured{Object: map[string]any{"apiVersion": "identity.ops.finnes.dev/v1alpha1", "kind": "GroupOfNames", "metadata": map[string]any{"name": resourceName, "namespace": ns}, "spec": map[string]any{"backendRef": map[string]any{"kind": "ProviderConfig", "name": name}, "cn": cn, "description": "Managed description", "initialMembers": []any{memberB}, "initialOwners": []any{memberB}}}}
	}
	create := func(xr *unstructured.Unstructured) *unstructured.Unstructured {
		t.Helper()
		obj, err := xrs.Create(ctx, xr, metav1.CreateOptions{FieldValidation: "Strict"})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = xrs.Delete(context.Background(), obj.GetName(), metav1.DeleteOptions{}) })
		return obj
	}
	poll := func(label string, check func() (bool, error)) {
		t.Helper()
		if err := wait.PollUntilContextTimeout(ctx, 500*time.Millisecond, 50*time.Second, true, func(context.Context) (bool, error) { return check() }); err != nil {
			t.Fatalf("%s: %v", label, err)
		}
	}
	ready := func(resourceName string) {
		t.Helper()
		poll("group Ready", func() (bool, error) {
			obj, err := xrs.Get(ctx, resourceName, metav1.GetOptions{})
			if err != nil {
				return false, err
			}
			conditions, _, _ := unstructured.NestedSlice(obj.Object, "status", "conditions")
			found := 0
			for _, v := range conditions {
				c := v.(map[string]any)
				if (c["type"] == "Ready" || c["type"] == "Synced") && c["status"] == "True" {
					found++
				}
			}
			return found == 2, nil
		})
	}
	original := create(makeGroup(name, "adopted-users"))
	ready(name)
	assertHumanState := func(members, owners []string) {
		t.Helper()
		entry := read(t, f.conn, dn)
		if uuidOf() != uuid || !slices.Equal(entry.GetAttributeValues("member"), members) || !slices.Equal(entry.GetAttributeValues("owner"), owners) {
			t.Fatal("reconciliation changed existing membership, ownership or UUID")
		}
	}
	assertHumanState([]string{memberA}, []string{memberA})
	modify := ldap.NewModifyRequest(dn, nil)
	modify.Replace("member", []string{memberA, memberB})
	modify.Replace("owner", []string{memberA, memberB})
	modify.Replace("description", []string{"drift"})
	if err := f.conn.Modify(modify); err != nil {
		t.Fatal(err)
	}
	poll("description repaired", func() (bool, error) {
		return read(t, f.conn, dn).GetAttributeValue("description") == "Managed description", nil
	})
	assertHumanState([]string{memberA, memberB}, []string{memberA, memberB})
	fresh := create(makeGroup(name+"-new", "created-users"))
	ready(fresh.GetName())
	created := read(t, f.conn, "cn=created-users,ou=groups,"+baseDN)
	if created.GetAttributeValue("member") != memberB || created.GetAttributeValue("owner") != memberB {
		t.Fatal("creation did not use explicit initial members and owners")
	}
	invalid := makeGroup(name+"-invalid", "bad,ou=users")
	if _, err := xrs.Create(ctx, invalid, metav1.CreateOptions{}); !apierrors.IsInvalid(err) {
		t.Fatal("unsafe cn was accepted")
	}
	invalid = makeGroup(name+"-empty", "empty-users")
	_ = unstructured.SetNestedSlice(invalid.Object, []any{}, "spec", "initialMembers")
	if _, err := xrs.Create(ctx, invalid, metav1.CreateOptions{}); !apierrors.IsInvalid(err) {
		t.Fatal("groupOfNames accepted empty bootstrap membership")
	}
	current, err := xrs.Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	_ = unstructured.SetNestedField(current.Object, "renamed", "spec", "cn")
	if _, err := xrs.Update(ctx, current, metav1.UpdateOptions{}); !apierrors.IsInvalid(err) {
		t.Fatal("LDAP group identity was mutable")
	}
	if err := xrs.Delete(ctx, name, metav1.DeleteOptions{}); err != nil {
		t.Fatal(err)
	}
	selector := "identity.ops.finnes.dev/group-uid=" + string(original.GetUID())
	poll("composed resources removed", func() (bool, error) {
		list, err := dyn.Resource(entryGVR).Namespace(ns).List(ctx, metav1.ListOptions{LabelSelector: selector})
		if err != nil {
			return false, err
		}
		secrets, err := kube.CoreV1().Secrets(ns).List(ctx, metav1.ListOptions{LabelSelector: selector})
		return len(list.Items) == 0 && len(secrets.Items) == 0, err
	})
	assertHumanState([]string{memberA, memberB}, []string{memberA, memberB})
	poll("group CR deleted", func() (bool, error) {
		_, err := xrs.Get(ctx, name, metav1.GetOptions{})
		return apierrors.IsNotFound(err), nil
	})
	create(makeGroup(name, "adopted-users"))
	ready(name)
	assertHumanState([]string{memberA, memberB}, []string{memberA, memberB})
	t.Log("GroupOfNames follows Canaille's LDAP model, repairs owned attributes, preserves human membership/owners, and retains stable identities across re-adoption")
}
