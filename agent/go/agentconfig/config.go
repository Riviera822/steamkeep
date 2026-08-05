// Package agentconfig parses vault-agent's configuration: flags with an
// environment-variable fallback (WP 2.2 brief: "config: one URL + API
// key", pick the simplest option that satisfies it).
//
// No config FILE format is implemented. Two reasons this is the simpler
// choice, not just the lazier one:
//   - A file that must hold VAULT_AGENT_API_KEY on disk needs its own
//     permission story and its own .gitignore-style "never commit this"
//     warning; an environment variable set by whatever launches the agent
//     (a Windows Scheduled Task's own "Run" field, a systemd unit's
//     Environment=/EnvironmentFile=, a shell wrapper) already has that
//     story solved by the launcher, not by this package.
//   - Passing the key via --api-key on the command line would leak it into
//     any process listing (`ps`, Task Manager's command-line column); an
//     env var does not.
//
// Every setting can still be set via CLI flag (convenient for a one-off
// manual `report` run) OR environment variable (the recommended way to
// supply VAULT_AGENT_API_KEY for a scheduled/service invocation) - a flag,
// if given, wins over its env var.
package agentconfig

import (
	"flag"
	"fmt"
	"io"
	"net/url"
	"os"
	"runtime"
	"strings"
	"time"
	"unicode"

	"github.com/Riviera822/steamvault/agent/report"
)

// Default report interval for --loop mode (plan §3: "periodically, e.g.
// every 30 min").
const DefaultReportInterval = 30 * time.Minute

// Env var names, also used as the flag-default source.
const (
	EnvServerURL   = "VAULT_AGENT_SERVER_URL"
	EnvAPIKey      = "VAULT_AGENT_API_KEY"
	EnvClientID    = "VAULT_AGENT_CLIENT_ID"
	EnvLibraryRoot = "VAULT_AGENT_LIBRARY_ROOT"
	EnvInterval    = "VAULT_AGENT_REPORT_INTERVAL"
)

// Config is vault-agent's fully validated, ready-to-use configuration.
type Config struct {
	ServerURL      string // e.g. "http://100.x.y.z:8080", no trailing slash
	APIKey         string // NEVER logged - see Redacted() and cmd/vault-agent's logger
	ClientID       string
	LibraryRoot    string
	ReportInterval time.Duration // only consulted in --loop mode
	Loop           bool
}

// Redacted returns a copy of cfg safe to log: APIKey is replaced with a
// fixed placeholder, never a partial/truncated key (a truncated key is
// still a key fragment).
func (c Config) Redacted() Config {
	c.APIKey = "<redacted>"
	return c
}

// Getenv matches os.Getenv's signature; Parse takes it as a parameter
// (rather than calling os.Getenv directly) so tests can inject a fake
// environment without mutating the real process environment.
type Getenv func(key string) string

// ParseError collects every configuration problem found, so a
// misconfigured agent gets ONE clear report instead of failing on the
// first flag and hiding the rest (the brief's "fail loudly" requirement,
// mirroring vault_api.config.Settings.from_env's style: raise with a
// specific, actionable message rather than defaulting a required value).
type ParseError struct {
	Errs []string
}

func (e *ParseError) Error() string {
	return "invalid vault-agent configuration:\n  - " + strings.Join(e.Errs, "\n  - ")
}

// flagSpec is the small set of flags shared by every present/future
// subcommand that needs a Config (today: only `report`).
type flagSpec struct {
	serverURL   string
	apiKey      string
	clientID    string
	libraryRoot string
	interval    string
	loop        bool
}

// Parse parses args (NOT including the subcommand name itself, e.g. for
// `vault-agent report --loop`, args is `["--loop"]`) against flags,
// applies the env-var fallback and defaults, and validates the result.
//
// name is used as the flag.FlagSet's name (for its own usage/error
// messages only). output receives flag.FlagSet's usage/error text (-h,
// an unknown flag, ...) - it MUST be the same writer the caller logs to,
// never left to default to the real os.Stderr, so a caller that captures
// its own output (tests; a future embedding) sees everything flag prints.
//
// SECURITY: flags are registered with EMPTY string defaults, never
// getenv(...). flag.FlagSet's usage text (printed on -h AND on an unknown
// flag) includes each flag's default value verbatim - registering
// getenv(EnvAPIKey) as the "api-key" flag's default would print the REAL
// API key to stdout/stderr the moment a user ran `vault-agent report -h`
// or fat-fingered a flag name, regardless of whether --api-key was even
// given (WP 2.2 review finding B1). The env fallback is applied
// separately in build(), AFTER flag parsing, where it never touches any
// usage/help text.
func Parse(name string, args []string, getenv Getenv, output io.Writer) (Config, error) {
	fs := flag.NewFlagSet(name, flag.ContinueOnError)
	fs.SetOutput(output)
	spec := flagSpec{}

	fs.StringVar(&spec.serverURL, "server-url", "",
		"vault-api base URL, e.g. http://100.x.y.z:8080 (env "+EnvServerURL+")")
	fs.StringVar(&spec.apiKey, "api-key", "",
		"vault-api X-Api-Key value (env "+EnvAPIKey+"; prefer the env var over this flag - "+
			"flag values are visible in process listings)")
	fs.StringVar(&spec.clientID, "client-id", "",
		"identifies this machine to vault-api, 1-64 chars (env "+EnvClientID+"; default: sanitized hostname)")
	fs.StringVar(&spec.libraryRoot, "library-root", "",
		"Steam install directory containing steamapps/ (env "+EnvLibraryRoot+"; default: OS-specific)")
	fs.StringVar(&spec.interval, "interval", "",
		"report interval for --loop mode, e.g. 30m (env "+EnvInterval+"; default: "+DefaultReportInterval.String()+")")
	fs.BoolVar(&spec.loop, "loop", false, "keep running, reporting every --interval (jittered) until SIGTERM/CTRL-C")

	if err := fs.Parse(args); err != nil {
		return Config{}, err // flag package already printed usage (to output) on -h/bad flag
	}

	return build(spec, getenv)
}

// build applies the env-var fallback (a non-empty flag value always wins
// over its env var - see spec field vs. getenv(...) below) and validates
// the result. Every flag defaults to "" from Parse, so this is the ONLY
// place a real secret/URL value is assembled - never inside a flag
// default, which flag.Usage() would echo back out.
func build(spec flagSpec, getenv Getenv) (Config, error) {
	var errs []string

	serverURL := spec.serverURL
	if serverURL == "" {
		serverURL = getenv(EnvServerURL)
	}
	serverURL = strings.TrimSpace(serverURL)
	if serverURL == "" {
		errs = append(errs, fmt.Sprintf(
			"server URL is required: set --server-url or %s", EnvServerURL,
		))
	} else if err := validateServerURL(serverURL); err != nil {
		errs = append(errs, err.Error())
	}
	serverURL = strings.TrimRight(serverURL, "/")

	apiKey := spec.apiKey
	if apiKey == "" {
		apiKey = getenv(EnvAPIKey)
	}
	// deliberately NOT trimmed beyond this: a key is opaque data, not text
	if apiKey == "" {
		errs = append(errs, fmt.Sprintf(
			"API key is required: set --api-key or (preferred) %s", EnvAPIKey,
		))
	}

	clientID := spec.clientID
	if clientID == "" {
		clientID = getenv(EnvClientID)
	}
	if clientID == "" {
		derived, derr := defaultClientID(hostnameFunc)
		if derr != nil {
			errs = append(errs, fmt.Sprintf(
				"client-id not given and could not be derived from the local hostname (%s); "+
					"set --client-id or %s explicitly", derr, EnvClientID,
			))
		} else {
			clientID = derived
		}
	}
	if clientID != "" {
		if err := report.ValidateClientID(clientID); err != nil {
			errs = append(errs, fmt.Sprintf("client-id: %s", err))
		}
	}

	libraryRoot := spec.libraryRoot
	if libraryRoot == "" {
		libraryRoot = getenv(EnvLibraryRoot)
	}
	libraryRoot = strings.TrimSpace(libraryRoot)
	if libraryRoot == "" {
		libraryRoot = defaultLibraryRoot(runtime.GOOS)
	}

	rawInterval := spec.interval
	if rawInterval == "" {
		rawInterval = getenv(EnvInterval)
	}

	interval := DefaultReportInterval
	if raw := strings.TrimSpace(rawInterval); raw != "" {
		parsed, err := time.ParseDuration(raw)
		if err != nil {
			errs = append(errs, fmt.Sprintf(
				"--interval/%s value %q is not a valid duration (e.g. \"30m\", \"1h\"): %s",
				EnvInterval, raw, err,
			))
		} else if parsed <= 0 {
			errs = append(errs, fmt.Sprintf(
				"--interval/%s must be positive, got %q", EnvInterval, raw,
			))
		} else {
			interval = parsed
		}
	}

	if len(errs) > 0 {
		return Config{}, &ParseError{Errs: errs}
	}

	return Config{
		ServerURL:      serverURL,
		APIKey:         apiKey,
		ClientID:       clientID,
		LibraryRoot:    libraryRoot,
		ReportInterval: interval,
		Loop:           spec.loop,
	}, nil
}

func validateServerURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("server URL %q is not a valid URL: %w", raw, err)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("server URL %q must use http:// or https://, got scheme %q", raw, u.Scheme)
	}
	if u.Host == "" {
		return fmt.Errorf("server URL %q has no host", raw)
	}
	return nil
}

// hostnameFunc is a package var (not a hardcoded os.Hostname() call) so
// config_test.go can substitute a deterministic value.
var hostnameFunc = os.Hostname

// defaultClientID sanitizes the local hostname into something that passes
// report.ValidateClientID: trims surrounding whitespace, replaces every
// non-printable rune (see report.ValidateClientID's doc comment for
// exactly which runes that excludes - control characters, invisible
// format characters like a zero-width joiner, private-use and unassigned
// codepoints, non-ASCII spaces) with "-", and truncates to
// report.MaxClientIDLength. Returns an error if os.Hostname() itself
// fails, or if the result is empty or exactly "." or ".." after
// sanitizing (report.ValidateClientID would reject those verbatim - this
// function does not disagree with it, it just gives a clearer error
// attributing the cause to hostname derivation specifically).
func defaultClientID(hostname func() (string, error)) (string, error) {
	name, err := hostname()
	if err != nil {
		return "", fmt.Errorf("os.Hostname failed: %w", err)
	}
	name = strings.TrimSpace(name)

	var b strings.Builder
	for _, r := range name {
		if !unicode.IsPrint(r) {
			b.WriteRune('-')
			continue
		}
		b.WriteRune(r)
	}
	sanitized := b.String()

	// Truncate to MaxClientIDLength RUNES (not bytes) - see
	// report.ValidateClientID's own rune-counting rationale.
	runes := []rune(sanitized)
	if len(runes) > report.MaxClientIDLength {
		runes = runes[:report.MaxClientIDLength]
	}
	sanitized = strings.TrimSpace(string(runes)) // truncation could re-expose trailing whitespace

	if sanitized == "" || sanitized == "." || sanitized == ".." {
		return "", fmt.Errorf("hostname %q sanitizes to an unusable client-id %q", name, sanitized)
	}
	return sanitized, nil
}

// defaultLibraryRoot returns the Steam install directory to use when
// neither --library-root nor VAULT_AGENT_LIBRARY_ROOT is given.
//
// No registry lookup (Windows) and no package-manager/well-known-path
// probing (Linux) is done in v1 - see the WP 2.2 brief. These are
// reasonable, documented starting points, not a guarantee the real
// install lives there; acf.DiscoverInstalled degrades to warnings (never
// a crash) if it doesn't.
func defaultLibraryRoot(goos string) string {
	switch goos {
	case "windows":
		return `C:\Program Files (x86)\Steam`
	default:
		// Linux/SteamOS (ADR-0002): the standard XDG data-home location a
		// real Steam client installs itself into. A full Linux/SteamOS
		// agent variant (library discovery beyond this default, systemd
		// packaging) is WP 2.5 - this default only keeps cross-builds for
		// linux/amd64 and linux/arm64 usable today, it is not that variant.
		home, err := os.UserHomeDir()
		if err != nil || home == "" {
			return ".local/share/Steam"
		}
		return home + "/.local/share/Steam"
	}
}
