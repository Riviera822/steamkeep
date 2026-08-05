// Command probe is a throwaway real-machine validation tool for WP 2.1b:
// it runs acf.DiscoverInstalled against a real Steam install directory
// and prints the result, so the Go port's output can be diffed against
// the Python executable specification's discover_installed() run
// against the SAME real machine (see agent/README.md's fixture/
// validation notes).
//
// This is deliberately NOT a production vault-agent entrypoint — the
// HTTP reporter (WP 2.2) and hosts-file mode (WP 2.3) are out of scope
// here. It exists so acf can be validated end-to-end against real
// appmanifest/libraryfolders.vdf files without committing any of that
// real data to the repo (see agent/README.md's fixture policy).
//
// Usage:
//
//	go run ./cmd/probe <library_root>
//	go run ./cmd/probe /mnt/c/steam
package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/Riviera822/steamvault/agent/acf"
)

func main() {
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "usage: %s <library_root>\n", os.Args[0])
	}
	flag.Parse()

	if flag.NArg() != 1 {
		flag.Usage()
		os.Exit(2)
	}
	libraryRoot := flag.Arg(0)

	apps, warnings := acf.DiscoverInstalled(libraryRoot)

	for _, w := range warnings {
		fmt.Fprintf(os.Stderr, "WARNING: %s\n", w)
	}

	fmt.Printf("discovered %d installed app(s) under %s:\n", len(apps), libraryRoot)
	for _, app := range apps {
		size := "unknown"
		if app.SizeOnDisk != nil {
			size = fmt.Sprintf("%d", *app.SizeOnDisk)
		}
		fmt.Printf("  appid=%-8s installed=%-5v stateflags=%-4d size=%-12s name=%q library=%s\n",
			app.AppID, app.Installed(), app.StateFlags, size, app.Name, app.LibraryPath)
	}

	fmt.Printf("\n%d warning(s)\n", len(warnings))
	if len(warnings) > 0 {
		os.Exit(1)
	}
}
