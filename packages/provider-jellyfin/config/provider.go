// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package config

import (
	_ "embed"
	"fmt"

	upstream "github.com/ThePhaseless/terraform-provider-jellyfin/internal/provider"
	ujconfig "github.com/crossplane/upjet/v2/pkg/config"
	"github.com/hashicorp/terraform-plugin-framework/provider"
)

//go:embed schema.json
var providerSchema []byte

const Module = "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane"

func FrameworkProvider() provider.Provider { return upstream.New("65dbe330d086")() }

func WithSchema(raw []byte, group string) *ujconfig.Provider {
	p := ujconfig.NewProvider(raw, "jellyfin", Module, []byte("name: jellyfin\nresources: {}\n"),
		ujconfig.WithRootGroup(group), ujconfig.WithFeaturesPackage("internal/features"),
		ujconfig.WithIncludeList([]string{}),
		ujconfig.WithTerraformPluginFrameworkIncludeList([]string{
			"jellyfin_plugin_configuration$", "jellyfin_plugin$", "jellyfin_plugin_repository$",
			"jellyfin_library$", "jellyfin_system_configuration$", "jellyfin_networking_configuration$",
			"jellyfin_branding_configuration$", "jellyfin_scheduled_task$",
		}), ujconfig.WithTerraformPluginFrameworkProvider(FrameworkProvider()))
	p.BasePackages = ujconfig.BasePackages{}
	p.AddResourceConfigurator("jellyfin_plugin_configuration", func(r *ujconfig.Resource) {
		// Optional pointer selectors keep an unused initProvider from looking up an empty Secret.
		field := r.TerraformResource.Schema["configuration_json"]
		field.Required, field.Optional = false, true
		r.MarkAsRequired("configuration_json")
	})
	for name, identifier := range map[string]string{
		"plugin_configuration": "plugin_id", "plugin": "name", "plugin_repository": "url", "library": "name", "scheduled_task": "key",
	} {
		p.AddResourceConfigurator("jellyfin_"+name, func(r *ujconfig.Resource) {
			r.ExternalName = ujconfig.ParameterAsIdentifier(identifier)
			// Plugin and repository IDs differ from their stable lookup keys.
			r.ExternalName.GetExternalNameFn = func(state map[string]any) (string, error) {
				value, ok := state[identifier].(string)
				if !ok || value == "" {
					return "", fmt.Errorf("missing external identifier %s", identifier)
				}
				return value, nil
			}
		})
	}
	for name, kind := range map[string]string{
		"plugin_configuration": "PluginConfiguration", "plugin": "Plugin", "plugin_repository": "PluginRepository",
		"library": "Library", "system_configuration": "SystemConfiguration", "networking_configuration": "NetworkingConfiguration",
		"branding_configuration": "BrandingConfiguration", "scheduled_task": "ScheduledTask",
	} {
		p.AddResourceConfigurator("jellyfin_"+name, func(r *ujconfig.Resource) {
			r.Kind, r.ShortGroup, r.UseAsync = kind, "", false
		})
	}
	p.ConfigureResources()
	return p
}

func Namespaced() *ujconfig.Provider { return WithSchema(providerSchema, "jellyfin.ops.finnes.dev") }
