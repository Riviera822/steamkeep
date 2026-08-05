package agentconfig

import (
	"bytes"
	"errors"
	"flag"
	"io"
	"runtime"
	"strings"
	"testing"
	"time"
)

func emptyEnv(string) string { return "" }

func envMap(m map[string]string) Getenv {
	return func(key string) string { return m[key] }
}

// parseDiscard is a thin Parse wrapper for tests that don't care about
// flag.FlagSet's usage/error output - it goes to io.Discard instead of
// being asserted on. Tests that DO care about that output (the B1
// redaction proofs below) call Parse directly with their own buffer.
func parseDiscard(args []string, getenv Getenv) (Config, error) {
	return Parse("report", args, getenv, io.Discard)
}

func TestParse_MinimalValidConfig(t *testing.T) {
	cfg, err := parseDiscard([]string{
		"--server-url", "http://100.64.0.1:8080",
		"--api-key", "s3cr3t",
		"--client-id", "gaming-pc",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ServerURL != "http://100.64.0.1:8080" {
		t.Errorf("ServerURL = %q", cfg.ServerURL)
	}
	if cfg.APIKey != "s3cr3t" {
		t.Errorf("APIKey = %q", cfg.APIKey)
	}
	if cfg.ClientID != "gaming-pc" {
		t.Errorf("ClientID = %q", cfg.ClientID)
	}
	if cfg.ReportInterval != DefaultReportInterval {
		t.Errorf("ReportInterval = %v, want default %v", cfg.ReportInterval, DefaultReportInterval)
	}
	if cfg.LibraryRoot == "" {
		t.Error("LibraryRoot should have a default, got empty")
	}
}

func TestParse_TrailingSlashStrippedFromServerURL(t *testing.T) {
	cfg, err := parseDiscard([]string{
		"--server-url", "http://100.64.0.1:8080/",
		"--api-key", "k",
		"--client-id", "pc",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ServerURL != "http://100.64.0.1:8080" {
		t.Errorf("ServerURL = %q, want trailing slash stripped", cfg.ServerURL)
	}
}

func TestParse_MissingServerURLFailsLoudly(t *testing.T) {
	_, err := parseDiscard([]string{"--api-key", "k", "--client-id", "pc"}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for a missing server URL")
	}
	if !strings.Contains(err.Error(), "server URL is required") {
		t.Errorf("error = %v, want it to mention the missing server URL", err)
	}
}

func TestParse_InvalidServerURLScheme(t *testing.T) {
	_, err := parseDiscard([]string{
		"--server-url", "ftp://example.com", "--api-key", "k", "--client-id", "pc",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for a non-http(s) scheme")
	}
	if !strings.Contains(err.Error(), "http://") {
		t.Errorf("error = %v, want it to mention the http(s) requirement", err)
	}
}

func TestParse_ServerURLWithNoHost(t *testing.T) {
	_, err := parseDiscard([]string{
		"--server-url", "http://", "--api-key", "k", "--client-id", "pc",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for a URL with no host")
	}
}

func TestParse_MissingAPIKeyFailsLoudly(t *testing.T) {
	_, err := parseDiscard([]string{
		"--server-url", "http://100.64.0.1:8080", "--client-id", "pc",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for a missing API key")
	}
	if !strings.Contains(err.Error(), "API key is required") {
		t.Errorf("error = %v, want it to mention the missing API key", err)
	}
}

func TestParse_APIKeyNeverAppearsInErrorMessages(t *testing.T) {
	// Even when the config is otherwise invalid (missing server URL), the
	// supplied API key must never leak into the aggregated error text.
	_, err := parseDiscard([]string{
		"--api-key", "super-secret-value", "--client-id", "..",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error")
	}
	if strings.Contains(err.Error(), "super-secret-value") {
		t.Fatalf("API key leaked into error message: %v", err)
	}
}

// --- B1 (review finding): the API key must never appear in flag.FlagSet's
// usage/error OUTPUT either - not just the aggregated ParseError text
// above. flag.Usage() (invoked on -h AND on an unrecognized flag) prints
// every flag's CURRENT DEFAULT VALUE. Registering getenv(EnvAPIKey) as
// the "api-key" flag's default (the pre-fix code) would print the real
// key the moment anyone ran `vault-agent report -h` or mistyped a flag -
// regardless of whether --api-key was given on that invocation at all.
// These tests set a canary key via the env (exactly the vector that
// leaked) and assert it never appears in Parse's output writer.

func TestParse_HelpOutputNeverContainsAPIKey(t *testing.T) {
	env := envMap(map[string]string{EnvAPIKey: "CANARY-HELP-LEAK-VALUE"})
	var out bytes.Buffer
	_, err := Parse("report", []string{"-h"}, env, &out)
	if !errors.Is(err, flag.ErrHelp) {
		t.Fatalf("err = %v, want flag.ErrHelp", err)
	}
	if strings.Contains(out.String(), "CANARY-HELP-LEAK-VALUE") {
		t.Fatalf("API key leaked into -h output:\n%s", out.String())
	}
}

func TestParse_UnknownFlagOutputNeverContainsAPIKey(t *testing.T) {
	env := envMap(map[string]string{EnvAPIKey: "CANARY-UNKNOWN-FLAG-LEAK"})
	var out bytes.Buffer
	_, err := Parse("report", []string{"--this-flag-does-not-exist"}, env, &out)
	if err == nil {
		t.Fatal("expected an error for an unrecognized flag")
	}
	if strings.Contains(out.String(), "CANARY-UNKNOWN-FLAG-LEAK") {
		t.Fatalf("API key leaked into unknown-flag output:\n%s", out.String())
	}
}

func TestParse_OutputGoesToTheGivenWriter(t *testing.T) {
	// Proves fs.SetOutput(output) is actually wired - a caller that
	// captures its own stderr must see -h's usage text there, not on the
	// real os.Stderr.
	var out bytes.Buffer
	_, _ = Parse("report", []string{"-h"}, emptyEnv, &out)
	if out.Len() == 0 {
		t.Fatal("expected -h usage text in the provided output writer, got nothing")
	}
	if !strings.Contains(out.String(), "server-url") {
		t.Errorf("output = %q, want it to list the server-url flag", out.String())
	}
}

func TestParse_InvalidExplicitClientID(t *testing.T) {
	_, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "..",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for client-id \"..\"")
	}
}

func TestParse_InvalidIntervalFormat(t *testing.T) {
	_, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
		"--interval", "notaduration",
	}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error for a malformed --interval")
	}
	if !strings.Contains(err.Error(), "not a valid duration") {
		t.Errorf("error = %v, want it to mention the invalid duration", err)
	}
}

func TestParse_NonPositiveIntervalRejected(t *testing.T) {
	for _, bad := range []string{"0s", "-5m"} {
		_, err := parseDiscard([]string{
			"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
			"--interval", bad,
		}, emptyEnv)
		if err == nil {
			t.Errorf("--interval=%q: expected an error, got nil", bad)
		}
	}
}

func TestParse_ValidIntervalOverridesDefault(t *testing.T) {
	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
		"--interval", "5m",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ReportInterval != 5*time.Minute {
		t.Errorf("ReportInterval = %v, want 5m", cfg.ReportInterval)
	}
}

func TestParse_EnvVarsUsedWhenFlagsOmitted(t *testing.T) {
	env := envMap(map[string]string{
		EnvServerURL:   "http://100.64.0.9:9090",
		EnvAPIKey:      "env-key",
		EnvClientID:    "env-client",
		EnvLibraryRoot: "/custom/steam",
		EnvInterval:    "10m",
	})
	cfg, err := parseDiscard(nil, env)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ServerURL != "http://100.64.0.9:9090" {
		t.Errorf("ServerURL = %q", cfg.ServerURL)
	}
	if cfg.APIKey != "env-key" {
		t.Errorf("APIKey = %q", cfg.APIKey)
	}
	if cfg.ClientID != "env-client" {
		t.Errorf("ClientID = %q", cfg.ClientID)
	}
	if cfg.LibraryRoot != "/custom/steam" {
		t.Errorf("LibraryRoot = %q", cfg.LibraryRoot)
	}
	if cfg.ReportInterval != 10*time.Minute {
		t.Errorf("ReportInterval = %v", cfg.ReportInterval)
	}
}

func TestParse_FlagOverridesEnvVar(t *testing.T) {
	env := envMap(map[string]string{
		EnvServerURL: "http://from-env:1",
		EnvAPIKey:    "env-key",
		EnvClientID:  "env-client",
	})
	cfg, err := parseDiscard([]string{"--server-url", "http://from-flag:2"}, env)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ServerURL != "http://from-flag:2" {
		t.Errorf("ServerURL = %q, want the flag value to win over the env var", cfg.ServerURL)
	}
}

func TestParse_LoopFlag(t *testing.T) {
	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc", "--loop",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !cfg.Loop {
		t.Error("Loop = false, want true")
	}
}

func TestParse_MultipleErrorsAreAllReported(t *testing.T) {
	_, err := parseDiscard([]string{"--client-id", ".."}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error")
	}
	msg := err.Error()
	if !strings.Contains(msg, "server URL is required") {
		t.Errorf("missing server-url error not reported: %v", msg)
	}
	if !strings.Contains(msg, "API key is required") {
		t.Errorf("missing api-key error not reported: %v", msg)
	}
}

func TestConfig_RedactedNeverExposesAPIKey(t *testing.T) {
	cfg := Config{APIKey: "top-secret"}
	red := cfg.Redacted()
	if red.APIKey == "top-secret" || strings.Contains(red.APIKey, "top-secret") {
		t.Errorf("Redacted().APIKey = %q, still contains the real key", red.APIKey)
	}
}

func TestDefaultClientID_SanitizesAndTruncates(t *testing.T) {
	longName := strings.Repeat("x", 100)
	got, err := defaultClientID(func() (string, error) { return longName, nil })
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len([]rune(got)) != 64 {
		t.Errorf("len(got) = %d, want truncated to 64", len([]rune(got)))
	}
}

func TestDefaultClientID_ReplacesControlCharacters(t *testing.T) {
	got, err := defaultClientID(func() (string, error) { return "pc\nname", nil })
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if strings.ContainsRune(got, '\n') {
		t.Errorf("got = %q, still contains a control character", got)
	}
}

func TestDefaultClientID_ReplacesInvisibleFormatCharacters(t *testing.T) {
	// S1: unicode.IsPrint (not just IsControl) is required to catch these -
	// a zero-width joiner is category Cf, not Cc.
	got, err := defaultClientID(func() (string, error) { return "pc‍name", nil }) // ZWJ
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if strings.ContainsRune(got, '‍') {
		t.Errorf("got = %q, still contains a zero-width joiner", got)
	}
}

func TestDefaultClientID_TrimsWhitespace(t *testing.T) {
	got, err := defaultClientID(func() (string, error) { return "  gaming-pc  ", nil })
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "gaming-pc" {
		t.Errorf("got = %q, want trimmed", got)
	}
}

func TestDefaultClientID_HostnameErrorIsPropagated(t *testing.T) {
	_, err := defaultClientID(func() (string, error) { return "", errHostname })
	if err == nil {
		t.Fatal("expected an error when os.Hostname fails")
	}
}

func TestDefaultClientID_EmptyAfterSanitizingIsAnError(t *testing.T) {
	_, err := defaultClientID(func() (string, error) { return "   ", nil })
	if err == nil {
		t.Fatal("expected an error for a hostname that sanitizes to empty")
	}
}

func TestParse_UsesDefaultClientIDWhenHostnameAvailable(t *testing.T) {
	orig := hostnameFunc
	hostnameFunc = func() (string, error) { return "test-host-01", nil }
	defer func() { hostnameFunc = orig }()

	cfg, err := parseDiscard([]string{"--server-url", "http://h:1", "--api-key", "k"}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ClientID != "test-host-01" {
		t.Errorf("ClientID = %q, want the sanitized hostname", cfg.ClientID)
	}
}

func TestParse_UnusableHostnameFailsLoudlyWhenNoClientIDGiven(t *testing.T) {
	orig := hostnameFunc
	hostnameFunc = func() (string, error) { return "", errHostname }
	defer func() { hostnameFunc = orig }()

	_, err := parseDiscard([]string{"--server-url", "http://h:1", "--api-key", "k"}, emptyEnv)
	if err == nil {
		t.Fatal("expected an error when hostname derivation fails and no --client-id was given")
	}
	if !strings.Contains(err.Error(), "client-id") {
		t.Errorf("error = %v, want it to mention client-id", err)
	}
}

func TestDefaultLibraryRoot_PerOS(t *testing.T) {
	win := defaultLibraryRoot("windows")
	if !strings.Contains(win, "Steam") {
		t.Errorf("windows default = %q, want it to mention Steam", win)
	}
	linux := defaultLibraryRoot("linux")
	if !strings.Contains(linux, ".local/share/Steam") {
		t.Errorf("linux default = %q, want the XDG Steam path", linux)
	}
}

func TestDefaultLibraryRoot_MatchesRuntimeGOOSConvention(t *testing.T) {
	// Sanity check that Parse's real default (using runtime.GOOS) doesn't
	// crash and produces a non-empty path on whatever OS the test runs on.
	cfg, err := parseDiscard([]string{
		"--server-url", "http://h:1", "--api-key", "k", "--client-id", "pc",
	}, emptyEnv)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want := defaultLibraryRoot(runtime.GOOS)
	if cfg.LibraryRoot != want {
		t.Errorf("LibraryRoot = %q, want %q", cfg.LibraryRoot, want)
	}
}

// errHostname is a fixed sentinel error for tests that need os.Hostname
// to fail.
var errHostname = &staticError{"hostname lookup failed"}

type staticError struct{ msg string }

func (e *staticError) Error() string { return e.msg }
