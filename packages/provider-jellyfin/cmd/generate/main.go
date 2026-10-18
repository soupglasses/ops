// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	localconfig "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/config"
	"github.com/crossplane/upjet/v2/pkg/pipeline"
	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
)

func attributes(in []*tfprotov6.SchemaAttribute) map[string]any {
	out := map[string]any{}
	for _, a := range in {
		v := map[string]any{"description": a.Description, "required": a.Required, "optional": a.Optional, "computed": a.Computed, "sensitive": a.Sensitive}
		if a.NestedType != nil {
			v["nested_type"] = map[string]any{"attributes": attributes(a.NestedType.Attributes), "nesting_mode": strings.ToLower(a.NestedType.Nesting.String())}
		} else {
			raw, err := a.Type.MarshalJSON()
			if err != nil {
				panic(err)
			}
			v["type"] = json.RawMessage(raw)
		}
		out[a.Name] = v
	}
	return out
}

func main() {
	response, err := providerserver.NewProtocol6(localconfig.FrameworkProvider())().GetProviderSchema(context.Background(), &tfprotov6.GetProviderSchemaRequest{})
	if err != nil {
		panic(err)
	}
	if len(response.Diagnostics) != 0 {
		panic(fmt.Sprint(response.Diagnostics))
	}
	resources := map[string]any{}
	for name, s := range response.ResourceSchemas {
		if len(s.Block.BlockTypes) != 0 {
			panic("unexpected nested blocks: " + name)
		}
		resources[name] = map[string]any{"version": s.Version, "block": map[string]any{"attributes": attributes(s.Block.Attributes)}}
	}
	doc := map[string]any{"format_version": "1.0", "provider_schemas": map[string]any{"registry.terraform.io/ThePhaseless/jellyfin": map[string]any{"resource_schemas": resources}}}
	raw, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		panic(err)
	}
	if err = os.WriteFile("config/schema.json", raw, 0600); err != nil {
		panic(err)
	}
	root, err := os.Getwd()
	if err != nil {
		panic(err)
	}
	pipeline.Run(localconfig.WithSchema(raw, "jellyfin.cluster.ops.finnes.dev"), localconfig.WithSchema(raw, "jellyfin.ops.finnes.dev"), root)
}
