// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package integration

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"image"
	"image/jpeg"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/go-ldap/ldap/v3"
	"github.com/hashicorp/terraform-plugin-sdk/v2/helper/schema"
	"github.com/hashicorp/terraform-plugin-sdk/v2/terraform"
	ldapclient "github.com/l-with/terraform-provider-ldap/client"
	ldapprovider "github.com/l-with/terraform-provider-ldap/ldap"
)

const baseDN = "dc=example,dc=test"
const fixturePassword = "disposable-local-fixture-only"

type fixture struct {
	conn     *ldap.Conn
	resource *schema.Resource
	meta     any
	port     int
}

func startLDAP(t *testing.T) *fixture {
	t.Helper()
	dir := t.TempDir()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()
	conf := fmt.Sprintf(`include /etc/openldap/schema/core.schema
include /etc/openldap/schema/cosine.schema
include /etc/openldap/schema/inetorgperson.schema
pidfile %s/slapd.pid
argsfile %s/slapd.args
database config
rootdn "cn=config"
rootpw %s
database mdb
maxsize 10485760
suffix "%s"
rootdn "cn=admin,%s"
rootpw %s
directory %s
access to * by dn.exact="cn=admin,%s" manage by * none
`, dir, dir, fixturePassword, baseDN, baseDN, fixturePassword, dir, baseDN)
	if modules := os.Getenv("LDAP_MODULE_PATH"); modules != "" {
		conf = fmt.Sprintf("modulepath %s\nmoduleload back_mdb\n", modules) + conf
	}
	configPath := filepath.Join(dir, "slapd.conf")
	if err = os.WriteFile(configPath, []byte(conf), 0600); err != nil {
		t.Fatal(err)
	}
	uri := "ldap://127.0.0.1:" + strconv.Itoa(port)
	listenURI := uri
	if os.Getenv("TEST_POD_IP") != "" {
		listenURI = "ldap://0.0.0.0:" + strconv.Itoa(port)
	}
	// Container runtimes may expose billions of descriptors; slapd sizes its listener table from this limit.
	cmd := exec.Command("/bin/sh", "-c", `ulimit -n 1024; exec "$@"`, "ldap-fixture", "slapd", "-f", configPath, "-h", listenURI, "-d", "0")
	var log bytes.Buffer
	cmd.Stdout = &log
	cmd.Stderr = &log
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait() })
	var conn *ldap.Conn
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
		conn, err = ldap.DialURL(uri)
		if err == nil {
			break
		}
		time.Sleep(25 * time.Millisecond)
	}
	if err != nil {
		t.Fatalf("slapd did not start: %v; %s", err, log.String())
	}
	t.Cleanup(func() { conn.Close() })
	// Use the deployed version's actual schema LDIF, not a replacement test schema.
	schemaFile := os.Getenv("CANAILLE_SCHEMA_LDIF")
	if schemaFile == "" {
		schemaFile = "../canaille-schema/oauth2-openldap.ldif"
	}
	load := exec.Command("ldapadd", "-x", "-H", uri, "-D", "cn=config", "-w", fixturePassword, "-f", schemaFile)
	if out, err := load.CombinedOutput(); err != nil {
		t.Fatalf("load Canaille 0.3.6 schema: %v: %s", err, out)
	}
	if err = conn.Bind("cn=admin,"+baseDN, fixturePassword); err != nil {
		t.Fatal(err)
	}
	add(t, conn, baseDN, map[string][]string{"objectClass": {"domain"}, "dc": {"example"}})
	for _, dn := range []string{"ou=oauth," + baseDN, "ou=clients,ou=oauth," + baseDN, "ou=groups," + baseDN, "ou=users," + baseDN} {
		parsed, _ := ldap.ParseDN(dn)
		add(t, conn, dn, map[string][]string{"objectClass": {"organizationalUnit"}, "ou": {parsed.RDNs[0].Attributes[0].Value}})
	}
	p := ldapprovider.Provider()
	if err := p.InternalValidate(); err != nil {
		t.Fatal(err)
	}
	diags := p.Configure(context.Background(), terraform.NewResourceConfigRaw(map[string]any{"host": "127.0.0.1", "port": port, "bind_user": "cn=admin," + baseDN, "bind_password": fixturePassword, "tls": false, "entry_attribute_names_case_sensitive": false}))
	if diags.HasError() {
		t.Fatal(diags)
	}
	t.Cleanup(func() { p.Meta().(*ldapclient.Client).Conn.Close() })
	return &fixture{conn: conn, resource: p.ResourcesMap["ldap_entry"], meta: p.Meta(), port: port}
}

func add(t *testing.T, c *ldap.Conn, dn string, attrs map[string][]string) {
	t.Helper()
	r := ldap.NewAddRequest(dn, nil)
	for k, v := range attrs {
		r.Attribute(k, v)
	}
	if err := c.Add(r); err != nil {
		t.Fatal(err)
	}
}
func read(t *testing.T, c *ldap.Conn, dn string) *ldap.Entry {
	t.Helper()
	r, err := c.Search(ldap.NewSearchRequest(dn, ldap.ScopeBaseObject, ldap.NeverDerefAliases, 1, 0, false, "(objectClass=*)", []string{"*"}, nil))
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Entries) != 1 {
		t.Fatal("expected exactly one entry")
	}
	return r.Entries[0]
}
func modify(t *testing.T, c *ldap.Conn, dn string, attrs map[string][]string) {
	t.Helper()
	r := ldap.NewModifyRequest(dn, nil)
	for k, v := range attrs {
		r.Replace(k, v)
	}
	if err := c.Modify(r); err != nil {
		t.Fatal(err)
	}
}
func encoded(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}
func desired(t *testing.T, dn string, attrs, defaults map[string][]string, owned []string) map[string]any {
	fields := make([]any, len(owned))
	for i, field := range owned {
		fields[i] = field
	}
	return map[string]any{"dn": dn, "data_json": encoded(t, attrs), "data_json_create_defaults": encoded(t, defaults), "restrict_attributes": fields}
}
func (f *fixture) apply(t *testing.T, state *terraform.InstanceState, want map[string]any) *terraform.InstanceState {
	t.Helper()
	d, err := f.resource.Diff(context.Background(), state, terraform.NewResourceConfigRaw(want), f.meta)
	if err != nil {
		t.Fatal(err)
	}
	if d == nil || d.Empty() {
		return state
	}
	out, diags := f.resource.Apply(context.Background(), state, d, f.meta)
	if diags.HasError() {
		t.Fatal(diags)
	}
	return out
}
func (f *fixture) refresh(t *testing.T, state *terraform.InstanceState) *terraform.InstanceState {
	t.Helper()
	out, diags := f.resource.RefreshWithoutUpgrade(context.Background(), state, f.meta)
	if diags.HasError() {
		t.Fatal(diags)
	}
	return out
}
func (f *fixture) stable(t *testing.T, state *terraform.InstanceState, want map[string]any) {
	t.Helper()
	d, err := f.resource.Diff(context.Background(), f.refresh(t, state), terraform.NewResourceConfigRaw(want), f.meta)
	if err != nil {
		t.Fatal(err)
	}
	if d != nil && !d.Empty() {
		t.Fatal("reconciliation did not converge")
	}
}

func TestCanailleOIDCLifecycle(t *testing.T) {
	f := startLDAP(t)
	dn := "oauthClientID=immich,ou=clients,ou=oauth," + baseDN
	attrs := map[string][]string{"objectClass": {"oauthClient"}, "oauthClientName": {"Immich"}, "oauthRedirectURIs": {"https://immich.example.test/auth/login"}, "oauthGrantType": {"authorization_code", "refresh_token"}, "oauthResponseType": {"code"}, "oauthScope": {"openid", "profile", "email"}, "oauthTokenEndpointAuthMethod": {"client_secret_basic"}, "oauthClientSecret": {"fixture-client-secret-1"}, "oauthIdTokenSignedResponseAlg": {"RS256"}}
	owned := []string{"objectClass", "oauthClientName", "oauthRedirectURIs", "oauthGrantType", "oauthResponseType", "oauthScope", "oauthTokenEndpointAuthMethod", "oauthClientSecret", "oauthIdTokenSignedResponseAlg", "oauthPostLogoutRedirectURI"}
	defaults := map[string][]string{"oauthIssueDate": {"20261017000000Z"}, "oauthAudience": {dn}}
	want := desired(t, dn, attrs, defaults, owned)
	state := f.apply(t, nil, want)
	f.stable(t, state, want)
	entry := read(t, f.conn, dn)
	if entry.GetAttributeValue("oauthClientID") != "immich" {
		t.Fatal("RDN was not materialized")
	}
	if entry.GetAttributeValue("oauthIssueDate") == "" || entry.GetAttributeValue("oauthAudience") != dn {
		t.Fatal("missing create-only metadata")
	}
	t.Log("created custom-schema confidential OIDC client; second reconciliation is a no-op")
	modify(t, f.conn, dn, map[string][]string{"oauthClientName": {"wrong"}, "oauthRedirectURIs": {"https://wrong.example.test/callback"}, "oauthIssueDate": {"20261018000000Z"}, "oauthPreconsent": {"TRUE"}})
	state = f.apply(t, f.refresh(t, state), want)
	f.stable(t, state, want)
	entry = read(t, f.conn, dn)
	if entry.GetAttributeValue("oauthClientName") != "Immich" || entry.GetAttributeValue("oauthRedirectURIs") != attrs["oauthRedirectURIs"][0] {
		t.Fatal("managed drift not repaired")
	}
	if entry.GetAttributeValue("oauthIssueDate") != "20261018000000Z" || entry.GetAttributeValue("oauthPreconsent") != "TRUE" {
		t.Fatal("runtime metadata overwritten")
	}
	t.Log("repaired redirects/name while preserving runtime issue date and preconsent")
	attrs["oauthPostLogoutRedirectURI"] = []string{"https://immich.example.test/logout"}
	attrs["oauthClientSecret"] = []string{"fixture-client-secret-2"}
	want = desired(t, dn, attrs, defaults, owned)
	state = f.apply(t, state, want)
	f.stable(t, state, want)
	if read(t, f.conn, dn).GetAttributeValue("oauthClientSecret") != "fixture-client-secret-2" {
		t.Fatal("credential rotation failed")
	}
	delete(attrs, "oauthPostLogoutRedirectURI")
	want = desired(t, dn, attrs, defaults, owned)
	state = f.apply(t, state, want)
	f.stable(t, state, want)
	if len(read(t, f.conn, dn).GetAttributeValues("oauthPostLogoutRedirectURI")) != 0 {
		t.Fatal("removed owned attribute persisted")
	}
	t.Log("rotated client credentials and removed an optional managed attribute")
	// Rebuild the SDK observation from a stable external DN, as Upjet does after restart.
	config := terraform.NewResourceConfigRaw(want)
	reconstructed := schema.TestResourceDataRaw(t, f.resource.Schema, want)
	reconstructed.SetId(dn)
	state = f.refresh(t, reconstructed.State())
	f.stable(t, state, want)
	if _, err := f.resource.Diff(context.Background(), state, config, f.meta); err != nil {
		t.Fatal(err)
	}
	t.Log("adopted the same LDAP entry using its DN with no tfstate file")
	public := map[string][]string{"objectClass": {"oauthClient"}, "oauthClientName": {"Public test"}, "oauthRedirectURIs": {"https://public.example.test/callback"}, "oauthTokenEndpointAuthMethod": {"none"}, "oauthGrantType": {"authorization_code"}, "oauthResponseType": {"code"}, "oauthScope": {"openid", "profile"}}
	publicDN := "oauthClientID=public,ou=clients,ou=oauth," + baseDN
	publicWant := desired(t, publicDN, public, map[string][]string{"oauthIssueDate": {"20261017000000Z"}}, owned)
	publicState := f.apply(t, nil, publicWant)
	f.stable(t, publicState, publicWant)
	if read(t, f.conn, publicDN).GetAttributeValue("oauthClientSecret") != "" {
		t.Fatal("public client unexpectedly has credentials")
	}
	t.Log("created a public client without a client secret")
	t.Run("CanailleRecognizesProvisionedClient", func(t *testing.T) {
		python := os.Getenv("CANAILLE_TEST_PYTHON")
		if python == "" {
			python = "../venv/bin/python"
		}
		if _, err := os.Stat(python); err != nil {
			t.Skip("set CANAILLE_TEST_PYTHON to an environment with canaille[ldap,oidc]==0.3.6")
		}
		cmd := exec.Command(python, "../tests/verify_canaille.py")
		cmd.Env = append(os.Environ(), "LDAP_TEST_URI=ldap://127.0.0.1:"+strconv.Itoa(f.port))
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("Canaille verification failed: %v: %s", err, out)
		} else {
			t.Log(string(out))
		}
	})
}

func TestHumanMembershipAndPhotoSurvive(t *testing.T) {
	f := startLDAP(t)
	humanDN := "uid=alice,ou=users," + baseDN
	var photo bytes.Buffer
	if err := jpeg.Encode(&photo, image.NewRGBA(image.Rect(0, 0, 2, 2)), nil); err != nil {
		t.Fatal(err)
	}
	add(t, f.conn, humanDN, map[string][]string{"objectClass": {"inetOrgPerson"}, "cn": {"Alice"}, "sn": {"Example"}, "jpegPhoto": {photo.String()}})
	personWant := desired(t, humanDN, map[string][]string{"objectClass": {"inetOrgPerson"}, "cn": {"Alice"}, "sn": {"Example"}}, nil, []string{"objectClass", "cn", "sn"})
	person := schema.TestResourceDataRaw(t, f.resource.Schema, personWant)
	person.SetId(humanDN)
	personState := f.refresh(t, person.State())
	modify(t, f.conn, humanDN, map[string][]string{"cn": {"wrong"}})
	personState = f.apply(t, f.refresh(t, personState), personWant)
	f.stable(t, personState, personWant)
	if read(t, f.conn, humanDN).GetAttributeValue("cn") != "Alice" || read(t, f.conn, humanDN).GetAttributeValue("jpegPhoto") != photo.String() {
		t.Fatal("person reconciliation failed to repair the name while preserving the binary JPEG")
	}
	dn := "cn=immich-users,ou=groups," + baseDN
	attrs := map[string][]string{"objectClass": {"groupOfNames"}, "description": {"Immich access"}}
	want := desired(t, dn, attrs, map[string][]string{"member": {"cn=admin," + baseDN}}, []string{"objectClass", "description"})
	state := f.apply(t, nil, want)
	f.stable(t, state, want)
	modify(t, f.conn, dn, map[string][]string{"member": {humanDN}, "description": {"wrong"}})
	state = f.apply(t, f.refresh(t, state), want)
	f.stable(t, state, want)
	entry := read(t, f.conn, dn)
	if entry.GetAttributeValue("member") != humanDN || entry.GetAttributeValue("description") != "Immich access" {
		t.Fatal("human membership was overwritten or description drift persisted")
	}
	if read(t, f.conn, humanDN).GetAttributeValue("jpegPhoto") != photo.String() {
		t.Fatal("human photo was changed")
	}
	t.Log("group bootstrap satisfies MUST member; human-managed membership and jpegPhoto survive drift repair")
}
