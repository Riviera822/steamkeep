package hostsfile

import (
	"os"
	"runtime"
	"strings"
)

// windowsFallbackSystemRoot is used only if %SystemRoot% is unset, which
// on a real Windows system does not happen.
const windowsFallbackSystemRoot = `C:\Windows`

// DefaultPath returns the system hosts file for the current platform:
//
//	windows  %SystemRoot%\System32\drivers\etc\hosts  (C:\Windows\... normally)
//	else     /etc/hosts
func DefaultPath() string { return defaultPathFor(runtime.GOOS, os.Getenv) }

// defaultPathFor is DefaultPath's testable core.
//
// The Windows path is assembled with literal backslashes rather than
// filepath.Join so the result is identical no matter which platform the
// process (or the test) happens to run on — this repo's Go tests execute
// under Linux/WSL, where filepath.Join would produce forward slashes and
// quietly weaken the assertion.
func defaultPathFor(goos string, getenv func(string) string) string {
	if goos != "windows" {
		return "/etc/hosts"
	}
	root := strings.TrimSpace(getenv("SystemRoot"))
	if root == "" {
		root = windowsFallbackSystemRoot
	}
	root = strings.TrimRight(root, `\/`)
	return root + `\System32\drivers\etc\hosts`
}

// ElevationHint returns the message to print when a mutation failed with a
// permission error: the platform's way of getting an elevated shell, plus
// the EXACT command to re-run there.
//
// # Why vault-agent has no elevation logic in v1 (deliberate, WP 2.3)
//
// Self-elevation (ShellExecute "runas" on Windows, re-exec under sudo on
// Linux) would mean shipping a binary that silently re-launches itself
// with administrator/root rights. That is a materially larger attack
// surface — the elevated child inherits arguments and environment from a
// context an attacker may control, and users are trained by it to click
// through UAC prompts raised by a background scheduled task. It also needs
// platform-specific code and a way to get output back out of the elevated
// process. Printing the exact command instead keeps the privileged step
// visible, deliberate, and in the user's own shell, at the cost of one
// copy-paste.
func ElevationHint(command string) string { return elevationHintFor(runtime.GOOS, command) }

func elevationHintFor(goos, command string) string {
	const why = "\n\nvault-agent never elevates itself on purpose: a binary that can silently\n" +
		"re-launch itself with administrator/root rights is a much larger attack\n" +
		"surface, so the privileged step stays visible and in your own shell."

	if goos == "windows" {
		return "The hosts file is only writable by an Administrator.\n" +
			"Open a new terminal as Administrator (press Start, type \"Terminal\",\n" +
			"then press Ctrl+Shift+Enter) and run exactly:\n\n" +
			"    " + command + why
	}
	return "The hosts file is only writable by root. Run exactly:\n\n" +
		"    sudo " + command + why
}
