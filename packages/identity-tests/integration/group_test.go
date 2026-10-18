//go:build system

// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package integration

import (
	"context"
	"fmt"
	"os"
	"os/exec"
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
		t.Skip("requires the group composition in the disposable integration fixture")
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
	pcGVR := schema.GroupVersionResource{Group: "ldap.finnes.dev", Version: "v1beta1", Resource: "providerconfigs"}
	xrGVR := schema.GroupVersionResource{Group: "ops.finnes.dev", Version: "v1alpha1", Resource: "groupofnames"}
	entryGVR := schema.GroupVersionResource{Group: "ldap.finnes.dev", Version: "v1alpha1", Resource: "entries"}
	xrs := dyn.Resource(xrGVR).Namespace(ns)
	f := startLDAP(t)
	secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: name}, Data: connectionSecretData(os.Getenv("TEST_POD_IP"), f.port)}
	if _, err := kube.CoreV1().Secrets(ns).Create(ctx, secret, metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = kube.CoreV1().Secrets(ns).Delete(context.Background(), name, metav1.DeleteOptions{}) })
	pc := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "ldap.finnes.dev/v1beta1", "kind": "ProviderConfig", "metadata": map[string]any{"name": name, "namespace": ns}, "spec": providerConnectionSpec(name, ns)}}
	if _, err := dyn.Resource(pcGVR).Namespace(ns).Create(ctx, pc, metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = dyn.Resource(pcGVR).Namespace(ns).Delete(context.Background(), name, metav1.DeleteOptions{})
	})
	sentinel := "uid=group-placeholder,ou=users," + baseDN
	add(t, f.conn, sentinel, map[string][]string{"objectClass": {"inetOrgPerson"}, "cn": {"Group sentinel (disabled)"}, "sn": {"Sentinel"}, "displayName": {"System: group placeholder (disabled)"}, "pwdEndTime": {"19700101000000Z"}})
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
		return &unstructured.Unstructured{Object: map[string]any{"apiVersion": "ops.finnes.dev/v1alpha1", "kind": "GroupOfNames", "metadata": map[string]any{"name": resourceName, "namespace": ns}, "spec": map[string]any{"backendRef": map[string]any{"kind": "ProviderConfig", "name": name}, "cn": cn, "description": "Managed description", "baseDN": "ou=groups," + baseDN, "sentinelDN": sentinel}}}
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
	retained := makeGroup(name, "adopted-users")
	_ = unstructured.SetNestedField(retained.Object, "Orphan", "spec", "deletionPolicy")
	original := create(retained)
	ready(name)
	assertHumanState := func(members, owners []string) {
		t.Helper()
		entry := read(t, f.conn, dn)
		members = append(slices.Clone(members), sentinel)
		actualMembers := slices.Clone(entry.GetAttributeValues("member"))
		slices.Sort(actualMembers)
		slices.Sort(members)
		actualOwners := slices.Clone(entry.GetAttributeValues("owner"))
		owners = slices.Clone(owners)
		slices.Sort(actualOwners)
		slices.Sort(owners)
		if uuidOf() != uuid || !slices.Equal(actualMembers, members) || !slices.Equal(actualOwners, owners) {
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
	if !slices.Equal(created.GetAttributeValues("member"), []string{sentinel}) || len(created.GetAttributeValues("owner")) != 0 {
		t.Fatal("new group granted human access or ownership instead of using only the sentinel")
	}
	python := os.Getenv("CANAILLE_TEST_PYTHON")
	if python == "" {
		t.Fatal("Canaille test runtime is required for the group integration contract")
	}
	verify := exec.CommandContext(ctx, python, "../tests/verify_canaille.py")
	verify.Env = append(os.Environ(), "LDAP_TEST_URI=ldap://127.0.0.1:"+fmt.Sprint(f.port), "LDAP_TEST_GROUP=created-users", "LDAP_TEST_SENTINEL_DN="+sentinel)
	if output, err := verify.CombinedOutput(); err != nil {
		t.Fatalf("Canaille membership and disabled placeholder: %v: %s", err, output)
	}
	if !slices.Equal(read(t, f.conn, "cn=created-users,ou=groups,"+baseDN).GetAttributeValues("member"), []string{sentinel}) {
		t.Fatal("Canaille changed the placeholder DN when removing the final human")
	}
	if err := xrs.Delete(ctx, fresh.GetName(), metav1.DeleteOptions{}); err != nil {
		t.Fatal(err)
	}
	poll("default Delete removes LDAP group", func() (bool, error) {
		_, err := f.conn.Search(ldapSearch("cn=created-users,ou=groups," + baseDN))
		return ldap.IsErrorWithCode(err, ldap.LDAPResultNoSuchObject), nil
	})
	invalid := makeGroup(name+"-invalid", "bad,ou=users")
	if _, err := xrs.Create(ctx, invalid, metav1.CreateOptions{}); !apierrors.IsInvalid(err) {
		t.Fatal("unsafe cn was accepted")
	}
	current, err := xrs.Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	oldDN := dn
	const renamedCN = "renamed-users"
	_ = unstructured.SetNestedField(current.Object, renamedCN, "spec", "cn")
	if _, err := xrs.Update(ctx, current, metav1.UpdateOptions{}); err != nil {
		t.Fatalf("rename group: %v", err)
	}
	dn = "cn=" + renamedCN + ",ou=groups," + baseDN
	poll("group renamed with stable LDAP UUID", func() (bool, error) {
		result, err := f.conn.Search(ldap.NewSearchRequest(dn, ldap.ScopeBaseObject, ldap.NeverDerefAliases, 1, 0, false, "(objectClass=*)", []string{"entryUUID"}, nil))
		if ldap.IsErrorWithCode(err, ldap.LDAPResultNoSuchObject) {
			return false, nil
		}
		if err != nil {
			return false, err
		}
		if len(result.Entries) != 1 || result.Entries[0].GetAttributeValue("entryUUID") != uuid {
			return false, nil
		}
		_, err = f.conn.Search(ldapSearch(oldDN))
		return ldap.IsErrorWithCode(err, ldap.LDAPResultNoSuchObject), nil
	})
	ready(name)
	assertHumanState([]string{memberA, memberB}, []string{memberA, memberB})
	composed, err := dyn.Resource(entryGVR).Namespace(ns).List(ctx, metav1.ListOptions{})
	if err != nil {
		t.Fatal(err)
	}
	var childNames []string
	for _, child := range composed.Items {
		if owner := metav1.GetControllerOf(&child); owner != nil && owner.UID == original.GetUID() {
			childNames = append(childNames, child.GetName())
		}
	}
	if len(childNames) != 1 {
		t.Fatalf("expected one controller-owned LDAP Entry, found %d", len(childNames))
	}
	if err := xrs.Delete(ctx, name, metav1.DeleteOptions{}); err != nil {
		t.Fatal(err)
	}
	poll("Orphan removes composed Kubernetes resources", func() (bool, error) {
		for _, childName := range childNames {
			_, err := dyn.Resource(entryGVR).Namespace(ns).Get(ctx, childName, metav1.GetOptions{})
			if err == nil {
				return false, nil
			}
			if !apierrors.IsNotFound(err) {
				return false, err
			}
		}
		return true, nil
	})
	assertHumanState([]string{memberA, memberB}, []string{memberA, memberB})
	poll("group CR deleted", func() (bool, error) {
		_, err := xrs.Get(ctx, name, metav1.GetOptions{})
		return apierrors.IsNotFound(err), nil
	})
	create(makeGroup(name, renamedCN))
	ready(name)
	assertHumanState([]string{memberA, memberB}, []string{memberA, memberB})
	t.Log("GroupOfNames follows Canaille's LDAP model, repairs owned attributes, preserves human membership/owners, and retains stable identities across re-adoption")
}
