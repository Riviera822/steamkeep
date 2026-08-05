// Package report builds and client-side validates the payload vault-agent
// posts to vault-api's `POST /v1/agent/installed` (WP 2.2, ADR-0002).
//
// The wire contract is THE CONTRACT this package targets, defined in
// api/vault_api/routers/agent.py + api/vault_api/agent_reports.py +
// api/vault_api/validation.py and documented in api/README.md's "Agent
// reports" section:
//
//	POST /v1/agent/installed
//	{"client_id": "<1-64 char id>", "appids": [<int ge=1>, ...]}
//
// BuildReport mirrors the server's validation rules LOCALLY, so a
// misconfigured agent fails fast with a clear message instead of spending a
// round trip on a 422 the server would have returned anyway (this package
// has no network dependency at all — see agent/go/client for the HTTP
// side).
package report

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/Riviera822/steamvault/agent/acf"
)

// MaxClientIDLength mirrors vault_api.agent_reports.MAX_CLIENT_ID_LENGTH.
const MaxClientIDLength = 64

// MaxAppIDs mirrors vault_api.agent_reports.MAX_APPIDS_PER_REPORT.
const MaxAppIDs = 10_000

// Payload is the exact JSON body `POST /v1/agent/installed` expects.
// AppIDs is always sorted ascending and de-duplicated — BuildReport is the
// only constructor and guarantees both, so the wire format is deterministic
// (easier to diff in logs/tests than an arbitrary order would be).
type Payload struct {
	ClientID string `json:"client_id"`
	AppIDs   []int  `json:"appids"`
}

// ValidationError is returned by BuildReport/ValidateClientID for any
// locally-rejected input. Kept as a distinct type (rather than a bare
// fmt.Errorf) so callers (cmd/vault-agent) can tell "this would have 422'd
// the server anyway" apart from a network/transport error and choose the
// right exit code without string-matching.
type ValidationError struct {
	Msg string
}

func (e *ValidationError) Error() string { return e.Msg }

func validationErrorf(format string, args ...any) *ValidationError {
	return &ValidationError{Msg: fmt.Sprintf(format, args...)}
}

// ValidateClientID applies the SAME rules
// vault_api.routers.agent.InstalledReportRequest._validate_client_id
// enforces server-side (api/vault_api/routers/agent.py), so a bad
// client_id (including the default derived from the local hostname, see
// agent/go/agentconfig) is rejected here rather than round-tripped to the
// server for a 422:
//
//   - 1-64 CHARACTERS (Unicode code points, matching Python's len() —
//     not bytes: utf8.RuneCountInString, not len(string)).
//   - No leading/trailing whitespace (it is an identity key: "pc" and
//     "pc " must not silently become two different clients).
//   - Every rune must be PRINTABLE (unicode.IsPrint), matching Python's
//     str.isprintable() rule the server enforces (WP 2.2 review finding
//     S1, verified against 46 parity cases spanning the server's
//     Pydantic validator): rejects ASCII control characters (NUL, tab,
//     CR, LF — the value is written into log lines, so a newline-like
//     character would let a malformed agent forge a fake log line),
//     Unicode format characters (Cf — e.g. a zero-width joiner/non-joiner,
//     invisible and would make two visually-identical ids compare
//     unequal), private-use (Co) and unassigned (Cn) codepoints, and
//     every space-like separator except the plain ASCII space (Zs other
//     than U+0020, plus Zl/Zp). A single ordinary emoji (category So,
//     Symbol) IS printable and stays allowed; a ZWJ-joined compound emoji
//     sequence is rejected because it contains a Cf joiner rune, exactly
//     matching the server's 422 for the same input. An earlier version of
//     this check used unicode.IsControl plus an explicit Zl/Zp
//     allowlist-complement, which missed Cf/Co/Cn and non-ASCII Zs
//     (e.g. NBSP) entirely — unicode.IsPrint is the single check the
//     Python parity sweep confirms matches every case.
//   - Not "." or "..".
func ValidateClientID(clientID string) error {
	length := utf8.RuneCountInString(clientID)
	if length < 1 || length > MaxClientIDLength {
		return validationErrorf(
			"client_id must be 1-%d characters, got %d (%q)",
			MaxClientIDLength, length, clientID,
		)
	}
	if strings.TrimSpace(clientID) != clientID {
		return validationErrorf(
			"client_id must not start or end with whitespace "+
				"(it is an identity key: %q and %q would be two clients): %q",
			clientID, clientID+" ", clientID,
		)
	}
	for _, r := range clientID {
		if !unicode.IsPrint(r) {
			return validationErrorf(
				"client_id must not contain non-printable characters "+
					"(control characters, invisible format characters like a zero-width "+
					"joiner, private-use or unassigned codepoints, non-ASCII spaces); "+
					"use a plain label such as a hostname: %q", clientID,
			)
		}
	}
	if clientID == "." || clientID == ".." {
		return validationErrorf(
			`client_id must not be "." or ".."; use a plain label such as a hostname`,
		)
	}
	return nil
}

// BuildReport filters apps down to the installed ones (StateFlags bit 4,
// see acf.InstalledApp.Installed), de-duplicates by app id, sorts
// ascending for a deterministic wire format, and validates the result
// against the server's rules before returning it.
//
// clientID is validated with ValidateClientID. Each app's AppID string is
// converted to an int and validated ge=1 (mirroring
// vault_api.validation.AppId's Field(ge=1)) — acf's own parser already
// enforces a strict ASCII-digit grammar on appid (agent/go/acf/
// appmanifest.go), so a conversion failure here would indicate a bug in
// that invariant rather than a real-world manifest, but it is still
// checked explicitly rather than assumed, and appid "0" (grammatically
// valid digits, but not ge=1) IS a real case this rejects. The full
// installed-app-count cap (MaxAppIDs) is checked AFTER de-duplication,
// matching the server, which counts distinct ids.
func BuildReport(apps []acf.InstalledApp, clientID string) (Payload, error) {
	if err := ValidateClientID(clientID); err != nil {
		return Payload{}, err
	}

	seen := make(map[int]struct{}, len(apps))
	ids := make([]int, 0, len(apps))
	for _, app := range apps {
		if !app.Installed() {
			continue
		}
		id, err := strconv.Atoi(app.AppID)
		if err != nil {
			return Payload{}, validationErrorf(
				"app %q: appid %q is not a valid integer: %s", app.Name, app.AppID, err,
			)
		}
		if id < 1 {
			return Payload{}, validationErrorf(
				"app %q: appid %d must be >= 1", app.Name, id,
			)
		}
		if _, dup := seen[id]; dup {
			continue
		}
		seen[id] = struct{}{}
		ids = append(ids, id)
	}

	if len(ids) > MaxAppIDs {
		return Payload{}, validationErrorf(
			"report has %d distinct installed app id(s), exceeds the %d cap", len(ids), MaxAppIDs,
		)
	}

	sort.Ints(ids)

	return Payload{ClientID: clientID, AppIDs: ids}, nil
}
