// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package main

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"net"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	xpresource "github.com/crossplane/crossplane-runtime/v2/pkg/resource"
	"github.com/crossplane/upjet/v2/pkg/terraform"
	"github.com/go-ldap/ldap/v3"
	ldapclient "github.com/l-with/terraform-provider-ldap/client"
	entryapi "github.com/soupglasses/ops/packages/provider-ldap/apis/namespaced/ldap/v1alpha1"
	configapi "github.com/soupglasses/ops/packages/provider-ldap/apis/namespaced/v1beta1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

const backendLabel = "identity.ops.finnes.dev/backend"
const ldapTimeout = 10 * time.Second

type cachedConnection struct {
	key    [32]byte
	client *ldapclient.Client
}

type connections struct {
	sync.Mutex
	clients map[string]cachedConnection
}

// validateEntryDN uses parsed RDNs so escaping and case changes cannot escape the ownership boundary.
func validateEntryDN(raw, namespace string, bases []string) error {
	dn, err := ldap.ParseDN(raw)
	if err != nil || len(dn.RDNs) < 2 {
		return fmt.Errorf("entry requires a valid child DN")
	}
	for _, rdn := range dn.RDNs {
		if len(rdn.Attributes) != 1 {
			return fmt.Errorf("multivalued RDNs are not permitted")
		}
	}
	if !strings.HasPrefix(strings.ToLower(dn.RDNs[0].Attributes[0].Value), namespace+".") {
		return fmt.Errorf("entry RDN must start with its namespace followed by a dot")
	}
	parent := &ldap.DN{RDNs: dn.RDNs[1:]}
	for _, rawBase := range bases {
		base, err := ldap.ParseDN(rawBase)
		if err != nil || len(base.RDNs) == 0 {
			return fmt.Errorf("provider has an invalid allowed base")
		}
		for _, rdn := range base.RDNs {
			if len(rdn.Attributes) != 1 {
				return fmt.Errorf("provider allowed bases must not contain multivalued RDNs")
			}
		}
		if parent.EqualFold(base) {
			return nil
		}
	}
	return fmt.Errorf("entry must be a direct child of an allowed base")
}

func providerSettings(ctx context.Context, k client.Client, entry *entryapi.Entry) (configapi.ProviderConfigSpec, string, error) {
	ref := entry.Spec.ProviderConfigReference
	if ref == nil || ref.Name == "" {
		return configapi.ProviderConfigSpec{}, "", fmt.Errorf("a provider configuration reference is required")
	}
	switch ref.Kind {
	case "ProviderConfig":
		pc := &configapi.ProviderConfig{}
		if err := k.Get(ctx, types.NamespacedName{Namespace: entry.Namespace, Name: ref.Name}, pc); err != nil {
			return configapi.ProviderConfigSpec{}, "", err
		}
		if pc.Spec.CredentialsSecretRef.Namespace != "" && pc.Spec.CredentialsSecretRef.Namespace != entry.Namespace {
			return configapi.ProviderConfigSpec{}, "", fmt.Errorf("namespaced provider credentials must remain in the entry namespace")
		}
		pc.Spec.CredentialsSecretRef.Namespace = entry.Namespace
		return pc.Spec, "ProviderConfig/" + entry.Namespace + "/" + ref.Name, nil
	case "ClusterProviderConfig":
		pc := &configapi.ClusterProviderConfig{}
		if err := k.Get(ctx, types.NamespacedName{Name: ref.Name}, pc); err != nil {
			return configapi.ProviderConfigSpec{}, "", err
		}
		namespace := &corev1.Namespace{}
		if err := k.Get(ctx, types.NamespacedName{Name: entry.Namespace}, namespace); err != nil {
			return configapi.ProviderConfigSpec{}, "", err
		}
		if namespace.Labels[backendLabel] != ref.Name {
			return configapi.ProviderConfigSpec{}, "", fmt.Errorf("entry namespace has not opted in to this directory backend")
		}
		if err := validateEntryDN(entry.Annotations["crossplane.io/external-name"], entry.Namespace, pc.Spec.AllowedBases); err != nil {
			return configapi.ProviderConfigSpec{}, "", err
		}
		if !pc.Spec.TLS || pc.Spec.CredentialsSecretRef.Namespace == "" {
			return configapi.ProviderConfigSpec{}, "", fmt.Errorf("cluster provider requires verified TLS and an explicit credential namespace")
		}
		return pc.Spec.ProviderConfigSpec, "ClusterProviderConfig/" + ref.Name, nil
	default:
		return configapi.ProviderConfigSpec{}, "", fmt.Errorf("unsupported provider configuration kind")
	}
}

func dialLDAP(spec configapi.ProviderConfigSpec, password string) (*ldapclient.Client, error) {
	if spec.Host == "" || spec.Port < 1 || spec.Port > 65535 || spec.BindDN == "" || password == "" {
		return nil, fmt.Errorf("provider host, port, bind DN and nonempty credentials are required")
	}
	scheme := "ldap"
	if spec.TLS {
		scheme = "ldaps"
	}
	address := (&url.URL{Scheme: scheme, Host: net.JoinHostPort(spec.Host, strconv.Itoa(spec.Port))}).String()
	conn, err := ldap.DialURL(address,
		ldap.DialWithDialer(&net.Dialer{Timeout: ldapTimeout}),
		ldap.DialWithTLSConfig(&tls.Config{MinVersion: tls.VersionTLS12, ServerName: spec.TLSServerName}),
	)
	if err != nil {
		return nil, fmt.Errorf("connect to directory: %w", err)
	}
	conn.SetTimeout(ldapTimeout)
	if err := conn.Bind(spec.BindDN, password); err != nil {
		conn.Close()
		return nil, fmt.Errorf("bind to directory: %w", err)
	}
	// Upstream CRUD only needs Conn. Do not call Configure, which mutates global settings and leaks connections.
	return &ldapclient.Client{Conn: conn}, nil
}

func (c *connections) setup(ctx context.Context, k client.Client, mg xpresource.Managed) (terraform.Setup, error) {
	entry, ok := mg.(*entryapi.Entry)
	if !ok {
		return terraform.Setup{}, fmt.Errorf("unsupported managed resource")
	}
	spec, name, err := providerSettings(ctx, k, entry)
	if err != nil {
		return terraform.Setup{}, err
	}
	secret := &corev1.Secret{}
	if err := k.Get(ctx, types.NamespacedName{Namespace: spec.CredentialsSecretRef.Namespace, Name: spec.CredentialsSecretRef.Name}, secret); err != nil {
		return terraform.Setup{}, err
	}
	password, ok := secret.Data[spec.CredentialsSecretRef.Key]
	if !ok || len(password) == 0 {
		return terraform.Setup{}, fmt.Errorf("provider credential key is missing or empty")
	}
	encoded, err := json.Marshal(struct {
		Spec     configapi.ProviderConfigSpec
		Password string
	}{spec, string(password)})
	if err != nil {
		return terraform.Setup{}, err
	}
	fingerprint := sha256.Sum256(encoded)
	c.Lock()
	defer c.Unlock()
	old, found := c.clients[name]
	if !found || old.key != fingerprint || old.client.Conn.IsClosing() {
		if found {
			old.client.Conn.Close()
			delete(c.clients, name)
		}
		next, err := dialLDAP(spec, string(password))
		if err != nil {
			return terraform.Setup{}, err
		}
		old = cachedConnection{key: fingerprint, client: next}
		if c.clients == nil {
			c.clients = make(map[string]cachedConnection)
		}
		c.clients[name] = old
	}
	return terraform.Setup{Meta: old.client, Configuration: map[string]any{}}, nil
}
