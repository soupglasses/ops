// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package integration

import (
	"bytes"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/go-ldap/ldap/v3"
)

const baseDN = "dc=example,dc=test"
const fixturePassword = "disposable-local-fixture-only"

type fixture struct {
	conn *ldap.Conn
	port int
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
overlay ppolicy
access to dn.exact="uid=group-placeholder,ou=users,%s" attrs=userPassword by * none
access to attrs=userPassword by anonymous auth by self write by * none
access to * by dn.exact="cn=admin,%s" manage by * none
`, dir, dir, fixturePassword, baseDN, baseDN, fixturePassword, dir, baseDN, baseDN)
	if modules := os.Getenv("LDAP_MODULE_PATH"); modules != "" {
		conf = fmt.Sprintf("modulepath %s\nmoduleload back_mdb\nmoduleload ppolicy\n", modules) + conf
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
	for _, dn := range []string{"ou=oauth," + baseDN, "ou=clients,ou=oauth," + baseDN, "ou=groups," + baseDN, "ou=users," + baseDN, "ou=system," + baseDN} {
		parsed, _ := ldap.ParseDN(dn)
		add(t, conn, dn, map[string][]string{"objectClass": {"organizationalUnit"}, "ou": {parsed.RDNs[0].Attributes[0].Value}})
	}
	return &fixture{conn: conn, port: port}
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
func connectionSecretData(host string, port int) map[string][]byte {
	return map[string][]byte{"connection.yaml": []byte(fmt.Sprintf("host: %s\nport: %d\nbindDN: cn=admin,%s\npassword: %s\ntls: false\n", host, port, baseDN, fixturePassword))}
}

func providerConnectionSpec(secretName, namespace string) map[string]any {
	return map[string]any{"credentials": map[string]any{"source": "Secret", "secretRef": map[string]any{"name": secretName, "namespace": namespace, "key": "connection.yaml"}}}
}

func attributeMap(values map[string][]string) map[string]any {
	out := make(map[string]any, len(values))
	for name, values := range values {
		v := make([]any, len(values))
		for i, value := range values {
			v[i] = value
		}
		out[name] = v
	}
	return out
}
