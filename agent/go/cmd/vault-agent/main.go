// Command vault-agent is the production PC listener (WP 2.2, ADR-0002,
// ADR-0005): discover the local Steam library, report the full installed
// list to vault-api, exit. Deliberately dumb (plan §3) - no control logic
// lives here, only discovery + reporting.
//
// Usage:
//
//	vault-agent report                 one-shot: discover -> report -> print -> exit 0/1
//	vault-agent report --loop           keep running, reporting every --interval (jittered)
//	                                     until SIGTERM/CTRL-C
//	vault-agent hosts apply|remove|status
//	                                    opt-in, DNS-free hosts-file mode (WP 2.3) - see
//	                                     hosts.go and agent/go/hostsfile
//
// One-shot is the PRIMARY mode (plan §7: a Windows Scheduled Task provides
// the timing); --loop exists for systemd (Phase 2.5's Linux/SteamOS
// packaging) where the service itself stays resident.
//
// Configuration is flags with an environment-variable fallback - see
// agent/go/agentconfig and agent/README.md's "Configuration" section.
// VAULT_AGENT_API_KEY is never logged in any code path here: every log
// line below is built from named fields, and the api key is never one of
// them - only handed to client.New, which itself never logs (see
// agent/go/client/client.go's package doc).
//
// Exit codes:
//
//	0  the report was sent and accepted (one-shot); or --loop exited
//	   cleanly on SIGTERM/CTRL-C; or -h/--help was requested
//	1  a runtime failure: local report validation failed, or the HTTP
//	   client gave up (network error, 401, 422, malformed response, ...)
//	2  a configuration/usage error (missing/invalid flag, no subcommand)
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"math/rand"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/Riviera822/steamvault/agent/acf"
	"github.com/Riviera822/steamvault/agent/agentconfig"
	"github.com/Riviera822/steamvault/agent/client"
	"github.com/Riviera822/steamvault/agent/report"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

// run contains all of main's logic, parameterized over args and output
// streams (io.Writer, not *os.File, specifically so a test can pass a
// bytes.Buffer and inspect exactly what would have been printed/logged -
// e.g. main_test.go's TestRun_APIKeyNeverAppearsInLoggedOutput, the
// redaction proof) instead of exec'ing a subprocess.
func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		printUsage(stderr)
		return 2
	}
	switch args[0] {
	case "report":
		return runReport(args, stdout, stderr)
	case "hosts":
		return runHosts(args[1:], stdout, stderr, programName())
	default:
		fmt.Fprintf(stderr, "unknown command %q\n\n", args[0])
		printUsage(stderr)
		return 2
	}
}

// programName is what the elevation hint tells the user to type. Taken
// from os.Args[0] so a renamed binary still prints a command that works.
func programName() string {
	if len(os.Args) == 0 || strings.TrimSpace(os.Args[0]) == "" {
		return "vault-agent"
	}
	return filepath.Base(os.Args[0])
}

func printUsage(w io.Writer) {
	fmt.Fprintln(w, "usage: vault-agent <command> [flags]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "commands:")
	fmt.Fprintln(w, "  report [--loop]              discover the local Steam library and report it to vault-api")
	fmt.Fprintln(w, "  hosts apply|remove|status    manage the optional hosts-file cache entry (opt-in, admin rights)")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "run 'vault-agent report -h' or 'vault-agent hosts' for the full flag list")
}

// runReport is the WP 2.2 `report` subcommand, unchanged by WP 2.3's
// addition of `hosts` beyond being lifted out of run()'s body. args
// INCLUDES the subcommand name at index 0.
func runReport(args []string, stdout, stderr io.Writer) int {
	logger := log.New(stderr, "", log.LstdFlags)

	// output goes to stderr (the SAME writer the caller passed in, not the
	// real os.Stderr) so -h/unknown-flag usage text is captured wherever
	// the rest of this process's logging goes (WP 2.2 review finding B1's
	// fs.SetOutput requirement).
	cfg, err := agentconfig.Parse("report", args[1:], os.Getenv, stderr)
	if err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0 // flag package already printed usage to stderr
		}
		logger.Printf("config error=%q", err)
		return 2
	}

	redacted := cfg.Redacted()
	logger.Printf("vault-agent starting server_url=%q client_id=%q library_root=%q loop=%v report_interval=%s api_key=%s",
		redacted.ServerURL, redacted.ClientID, redacted.LibraryRoot, redacted.Loop, redacted.ReportInterval, redacted.APIKey)

	httpClient := client.New(cfg.ServerURL, cfg.APIKey)

	if !cfg.Loop {
		if reportOnce(context.Background(), logger, stdout, cfg, httpClient) {
			return 0
		}
		return 1
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	runLoop(ctx, logger, stdout, cfg, httpClient)
	return 0
}

// reportOnce discovers + reports exactly once and prints a human-readable
// result line to stdout. Returns true on success.
func reportOnce(ctx context.Context, logger *log.Logger, stdout io.Writer, cfg agentconfig.Config, c *client.Client) bool {
	apps, warnings := acf.DiscoverInstalled(cfg.LibraryRoot)
	for _, w := range warnings {
		logger.Printf("discover warning=%q", w.Message)
	}

	payload, err := report.BuildReport(apps, cfg.ClientID)
	if err != nil {
		logger.Printf("report build failed error=%q", err)
		return false
	}
	logger.Printf("report built installed_count=%d client_id=%q", len(payload.AppIDs), payload.ClientID)

	// A single HTTP attempt (with client.Client's own internal retries) is
	// bounded generously - this is a small JSON POST, not a download; 2
	// minutes covers even a client.Client configured with a larger-than-
	// default retry budget on a flaky link without hanging the scheduled
	// task indefinitely.
	reqCtx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()

	result, err := c.ReportInstalled(reqCtx, payload)
	if err != nil {
		logger.Printf("report send failed error=%q", err)
		return false
	}

	fmt.Fprintf(stdout, "reported %d installed app(s) for client_id=%s: added=%v removed=%v first_report=%v\n",
		result.Received, result.ClientID, result.Added, result.Removed, result.FirstReport)
	logger.Printf("report accepted received=%d added=%d removed=%d first_report=%v",
		result.Received, len(result.Added), len(result.Removed), result.FirstReport)
	return true
}

// runLoop reports on cfg.ReportInterval (+/- jitter) until ctx is
// canceled (main wires ctx from signal.NotifyContext for SIGTERM/CTRL-C;
// taking ctx as a parameter rather than constructing it here also lets a
// test drive runLoop directly with an ordinary cancelable context instead
// of sending the test process a real OS signal). Report failures are
// logged but never stop the loop - the whole point of --loop is to keep
// trying across a VPN/network outage (plan §7) rather than give up after
// one bad interval.
func runLoop(ctx context.Context, logger *log.Logger, stdout io.Writer, cfg agentconfig.Config, c *client.Client) {
	rng := rand.New(rand.NewSource(time.Now().UnixNano()))
	logger.Printf("loop mode started interval=%s", cfg.ReportInterval)

	for {
		reportOnce(ctx, logger, stdout, cfg, c)

		// Checked BEFORE logging "sleeping until next report": if shutdown
		// arrived while reportOnce was running, this exits right away -
		// otherwise the log would print a "sleeping for Xm" line and then
		// immediately exit without ever having slept, which misrepresents
		// what actually happened (WP 2.2 review nitpick).
		select {
		case <-ctx.Done():
			logger.Printf("shutdown signal received, exiting cleanly")
			return
		default:
		}

		delay := jitteredInterval(cfg.ReportInterval, rng)
		logger.Printf("sleeping until next report in=%s", delay)

		select {
		case <-ctx.Done():
			logger.Printf("shutdown signal received, exiting cleanly")
			return
		case <-time.After(delay):
		}
	}
}

// jitteredInterval returns interval +/- up to 10%, so many agents on the
// same network configured with the same interval don't all report in
// lockstep against the same vault-api instance.
func jitteredInterval(interval time.Duration, rng *rand.Rand) time.Duration {
	if interval <= 0 {
		return interval
	}
	const jitterFraction = 0.10
	spread := float64(interval) * jitterFraction
	offset := (rng.Float64()*2 - 1) * spread // in [-spread, +spread]
	result := time.Duration(float64(interval) + offset)
	if result < 0 {
		result = 0
	}
	return result
}
