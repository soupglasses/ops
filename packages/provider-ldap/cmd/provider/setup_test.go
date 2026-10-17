// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package main

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"strconv"
	"sync"

	ber "github.com/go-asn1-ber/asn1-ber"
	ldapclient "github.com/l-with/terraform-provider-ldap/client"
	"k8s.io/apimachinery/pkg/types"
	"testing"

	entryapi "github.com/soupglasses/ops/packages/provider-ldap/apis/namespaced/ldap/v1alpha1"
	configapi "github.com/soupglasses/ops/packages/provider-ldap/apis/namespaced/v1beta1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestEntryOwnership(t *testing.T) {
	bases := []string{"ou=system,dc=example,dc=test", "ou=groups,dc=example,dc=test", "ou=clients,ou=oauth,dc=example,dc=test"}
	for _, tc := range []struct {
		name, dn string
		allowed  bool
	}{
		{"client", "oauthClientID=media.jellyfin,ou=clients,ou=oauth,dc=example,dc=test", true},
		{"service", "uid=media.jellyfin,ou=system,dc=example,dc=test", true},
		{"group", "cn=media.viewers,ou=groups,dc=example,dc=test", true},
		{"case normalized", "UID=MEDIA.Jellyfin,OU=SYSTEM,DC=EXAMPLE,DC=TEST", true},
		{"escaped dot", `uid=media\2ejellyfin,ou=system,dc=example,dc=test`, true},
		{"other namespace", "uid=identity.jellyfin,ou=system,dc=example,dc=test", false},
		{"ambiguous prefix", "uid=mediacenter.jellyfin,ou=system,dc=example,dc=test", false},
		{"missing separator", "uid=media,ou=system,dc=example,dc=test", false},
		{"human user", "uid=media.jellyfin,ou=users,dc=example,dc=test", false},
		{"base itself", "ou=system,dc=example,dc=test", false},
		{"descendant", "uid=media.jellyfin,ou=other,ou=system,dc=example,dc=test", false},
		{"escaped separator", `uid=media.jellyfin\,ou=system,dc=example,dc=test`, false},
		{"multivalued leaf", "uid=media.jellyfin+cn=admin,ou=system,dc=example,dc=test", false},
		{"multivalued parent", "uid=media.jellyfin,ou=system+cn=admin,dc=example,dc=test", false},
		{"invalid", "not a DN", false},
		{"empty", "", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := validateEntryDN(tc.dn, "media", bases)
			if (err == nil) != tc.allowed {
				t.Fatalf("allowed=%v, error=%v", tc.allowed, err)
			}
		})
	}
	if err := validateEntryDN("uid=media.jellyfin,ou=system,dc=example,dc=test", "media", nil); err == nil {
		t.Fatal("empty allowed bases accepted")
	}
}

func TestClusterProviderAuthorization(t *testing.T) {
	for _, tc := range []struct {
		name, label, dn string
		tls, allowed    bool
	}{
		{"opted in", "directory", "uid=media.jellyfin,ou=system,dc=example,dc=test", true, true},
		{"not opted in", "", "uid=media.jellyfin,ou=system,dc=example,dc=test", true, false},
		{"different backend", "other", "uid=media.jellyfin,ou=system,dc=example,dc=test", true, false},
		{"other namespace DN", "directory", "uid=identity.jellyfin,ou=system,dc=example,dc=test", true, false},
		{"plaintext root bind", "directory", "uid=media.jellyfin,ou=system,dc=example,dc=test", false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			scheme := runtime.NewScheme()
			if err := corev1.AddToScheme(scheme); err != nil {
				t.Fatal(err)
			}
			if err := configapi.AddToScheme(scheme); err != nil {
				t.Fatal(err)
			}
			pc := &configapi.ClusterProviderConfig{ObjectMeta: metav1.ObjectMeta{Name: "directory"}, Spec: configapi.ClusterProviderConfigSpec{
				ProviderConfigSpec: configapi.ProviderConfigSpec{TLS: tc.tls, CredentialsSecretRef: configapi.CredentialsReference{Namespace: "identity", Name: "openldap", Key: "LDAP_ROOT_PASSWORD"}},
				AllowedBases:       []string{"ou=system,dc=example,dc=test"},
			}}
			ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: "media", Labels: map[string]string{backendLabel: tc.label}}}
			k := fake.NewClientBuilder().WithScheme(scheme).WithObjects(pc, ns).Build()
			entry := testEntry(t, "ClusterProviderConfig", "directory")
			entry.Annotations = map[string]string{"crossplane.io/external-name": tc.dn}
			spec, _, err := providerSettings(context.Background(), k, entry)
			if (err == nil) != tc.allowed {
				t.Fatalf("allowed=%v, error=%v", tc.allowed, err)
			}
			if tc.allowed && spec.CredentialsSecretRef.Namespace != "identity" {
				t.Fatal("central credential namespace lost")
			}
		})
	}
}

func TestNamespacedProviderCannotReadAnotherNamespace(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := configapi.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	for _, namespace := range []string{"", "media", "identity"} {
		pc := &configapi.ProviderConfig{ObjectMeta: metav1.ObjectMeta{Namespace: "media", Name: "local"}, Spec: configapi.ProviderConfigSpec{CredentialsSecretRef: configapi.CredentialsReference{Namespace: namespace}}}
		k := fake.NewClientBuilder().WithScheme(scheme).WithObjects(pc).Build()
		spec, _, err := providerSettings(context.Background(), k, testEntry(t, "ProviderConfig", "local"))
		if namespace == "identity" {
			if err == nil {
				t.Fatal("cross-namespace credential reference accepted")
			}
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		if spec.CredentialsSecretRef.Namespace != "media" {
			t.Fatal("local credential namespace not resolved")
		}
	}
}

func testEntry(t *testing.T, kind, name string) *entryapi.Entry {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"metadata": map[string]any{"namespace": "media"}, "spec": map[string]any{"providerConfigRef": map[string]any{"kind": kind, "name": name}}})
	if err != nil {
		t.Fatal(err)
	}
	entry := &entryapi.Entry{}
	if err := json.Unmarshal(raw, entry); err != nil {
		t.Fatal(err)
	}
	return entry
}

// Exercise the real LDAP bind and cached socket lifecycle without a directory process.
func TestCredentialRotationReconnects(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var sockets []net.Conn
	t.Cleanup(func() {
		listener.Close()
		mu.Lock()
		defer mu.Unlock()
		for _, socket := range sockets {
			socket.Close()
		}
	})
	go func() {
		for {
			socket, err := listener.Accept()
			if err != nil {
				return
			}
			mu.Lock()
			sockets = append(sockets, socket)
			mu.Unlock()
			go func() {
				defer socket.Close()
				if _, err := ber.ReadPacket(socket); err != nil {
					return
				}
				// LDAP BindResponse: message ID 1, successful result, empty matched DN and diagnostic.
				if _, err := socket.Write([]byte{0x30, 0x0c, 0x02, 0x01, 0x01, 0x61, 0x07, 0x0a, 0x01, 0x00, 0x04, 0x00, 0x04, 0x00}); err != nil {
					return
				}
				_, _ = io.Copy(io.Discard, socket)
			}()
		}
	}()
	_, rawPort, err := net.SplitHostPort(listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(rawPort)
	if err != nil {
		t.Fatal(err)
	}
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := configapi.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	pc := &configapi.ProviderConfig{ObjectMeta: metav1.ObjectMeta{Namespace: "media", Name: "local"}, Spec: configapi.ProviderConfigSpec{
		Host: "127.0.0.1", Port: port, BindDN: "cn=fixture", TLS: false,
		CredentialsSecretRef: configapi.CredentialsReference{Name: "credentials", Key: "password"},
	}}
	secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Namespace: "media", Name: "credentials"}, Data: map[string][]byte{"password": []byte("disposable-one")}}
	k := fake.NewClientBuilder().WithScheme(scheme).WithObjects(pc, secret).Build()
	pool := &connections{}
	entry := testEntry(t, "ProviderConfig", "local")
	first, err := pool.setup(context.Background(), k, entry)
	if err != nil {
		t.Fatal(err)
	}
	firstConn := first.Meta.(*ldapclient.Client).Conn
	same, err := pool.setup(context.Background(), k, entry)
	if err != nil {
		t.Fatal(err)
	}
	if same.Meta.(*ldapclient.Client).Conn != firstConn {
		t.Fatal("unchanged credentials opened another connection")
	}
	if err := k.Get(context.Background(), types.NamespacedName{Namespace: "media", Name: "credentials"}, secret); err != nil {
		t.Fatal(err)
	}
	secret.Data["password"] = []byte("disposable-two")
	if err := k.Update(context.Background(), secret); err != nil {
		t.Fatal(err)
	}
	rotated, err := pool.setup(context.Background(), k, entry)
	if err != nil {
		t.Fatal(err)
	}
	if rotated.Meta.(*ldapclient.Client).Conn == firstConn {
		t.Fatal("rotated credentials reused the previous connection")
	}
	if !firstConn.IsClosing() {
		t.Fatal("old credential connection was not closed")
	}
	rotated.Meta.(*ldapclient.Client).Conn.Close()
	recovered, err := pool.setup(context.Background(), k, entry)
	if err != nil {
		t.Fatal(err)
	}
	if recovered.Meta.(*ldapclient.Client).Conn == rotated.Meta.(*ldapclient.Client).Conn {
		t.Fatal("closed socket was not replaced")
	}
	recovered.Meta.(*ldapclient.Client).Conn.Close()
}
