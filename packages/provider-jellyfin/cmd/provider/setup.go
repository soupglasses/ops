// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package main

import (
	"context"
	"fmt"

	configapi "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/apis/namespaced/v1beta1"
	localconfig "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/config"
	xpresource "github.com/crossplane/crossplane-runtime/v2/pkg/resource"
	"github.com/crossplane/upjet/v2/pkg/terraform"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

func setup(ctx context.Context, k client.Client, resource xpresource.Managed) (terraform.Setup, error) {
	// This controller serves only its application namespace; credentials cannot cross that boundary.
	managed, ok := resource.(xpresource.TypedProviderConfigReferencer)
	if !ok {
		return terraform.Setup{}, fmt.Errorf("namespaced resource required")
	}
	ref := managed.GetProviderConfigReference()
	if ref == nil || ref.Kind != "ProviderConfig" {
		return terraform.Setup{}, fmt.Errorf("local ProviderConfig required")
	}
	pc := &configapi.ProviderConfig{}
	if err := k.Get(ctx, types.NamespacedName{Namespace: resource.GetNamespace(), Name: ref.Name}, pc); err != nil {
		return terraform.Setup{}, err
	}
	secret := &corev1.Secret{}
	if err := k.Get(ctx, types.NamespacedName{Namespace: resource.GetNamespace(), Name: pc.Spec.PasswordSecretRef.Name}, secret); err != nil {
		return terraform.Setup{}, err
	}
	password := secret.Data[pc.Spec.PasswordSecretRef.Key]
	if len(password) == 0 {
		return terraform.Setup{}, fmt.Errorf("administrator credential key missing or empty")
	}
	username := pc.Spec.Username
	if pc.Spec.UsernameSecretRef != nil {
		if username != "" {
			return terraform.Setup{}, fmt.Errorf("set username or usernameSecretRef")
		}
		userSecret := &corev1.Secret{}
		if err := k.Get(ctx, types.NamespacedName{Namespace: resource.GetNamespace(), Name: pc.Spec.UsernameSecretRef.Name}, userSecret); err != nil {
			return terraform.Setup{}, err
		}
		username = string(userSecret.Data[pc.Spec.UsernameSecretRef.Key])
	}
	if username == "" {
		return terraform.Setup{}, fmt.Errorf("administrator username missing or empty")
	}
	return terraform.Setup{FrameworkProvider: localconfig.FrameworkProvider(), Configuration: terraform.ProviderConfiguration{
		"endpoint": pc.Spec.Endpoint, "username": username, "password": string(password),
	}}, nil
}
