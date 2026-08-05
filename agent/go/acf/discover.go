package acf

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Warning is a non-fatal finding surfaced by DiscoverInstalled. Go has no
// equivalent of Python's ambient `logging.warning` convention, so instead
// of logging internally this package returns warnings to the caller, who
// decides how to surface them (stderr, a structured logger, a metrics
// counter, ...). See agent/README.md's "Resilience contract" table for
// the exact situation -> warning mapping this mirrors from the Python
// spec (agent/vault_agent/acf.py's discover_installed).
type Warning struct {
	Message string
}

func (w Warning) String() string { return w.Message }

// DiscoverInstalled discovers all installed apps across every Steam
// library.
//
// libraryRoot is the main Steam install directory (the one that contains
// steamapps/libraryfolders.vdf — e.g. C:\Steam on Windows,
// ~/.local/share/Steam on Linux/SteamOS per ADR-0002). That file lists
// every library folder Steam knows about, including the main one itself.
//
// Tolerant by design, never returns an error for per-file problems —
// problems are reported as Warnings alongside the (possibly partial)
// result:
//   - Missing or corrupt libraryfolders.vdf falls back to treating
//     libraryRoot as the only library.
//   - Missing or corrupt appmanifest files are skipped.
//   - Missing library directories on disk are skipped.
//   - Duplicate app IDs across libraries: first one found wins, later
//     ones are skipped.
//
// Returns the list of InstalledApp in discovery order (an unreadable
// libraryRoot itself still returns an empty list and no crash, only
// warnings — mirroring the Python spec's resilience contract).
func DiscoverInstalled(libraryRoot string) ([]InstalledApp, []Warning) {
	var warnings []Warning

	libraryFoldersPath := filepath.Join(libraryRoot, "steamapps", "libraryfolders.vdf")

	var libraryPaths []string
	parsed, err := ParseLibraryFoldersFile(libraryFoldersPath)
	if err != nil {
		warnings = append(warnings, Warning{fmt.Sprintf(
			"could not read/parse %s (%s); falling back to treating %s as the only library",
			libraryFoldersPath, err, libraryRoot)})
		libraryPaths = []string{libraryRoot}
	} else if len(parsed) == 0 {
		warnings = append(warnings, Warning{fmt.Sprintf(
			"%s parsed but listed no library paths; falling back to %s",
			libraryFoldersPath, libraryRoot)})
		libraryPaths = []string{libraryRoot}
	} else {
		libraryPaths = parsed
	}

	// De-duplicate while preserving order (libraryfolders.vdf shouldn't
	// list the same path twice, but tolerate it).
	seenLibraries := map[string]bool{}
	var orderedLibraries []string
	for _, lib := range libraryPaths {
		if !seenLibraries[lib] {
			seenLibraries[lib] = true
			orderedLibraries = append(orderedLibraries, lib)
		}
	}

	var apps []InstalledApp
	seenAppIDs := map[string]string{} // appid -> library path that won

	for _, lib := range orderedLibraries {
		steamappsDir := filepath.Join(lib, "steamapps")

		// Deliberately os.ReadDir + manual "appmanifest_*.acf" prefix/
		// suffix matching instead of filepath.Glob: Glob applies
		// Match-style pattern parsing to EVERY segment of the joined
		// path, and on non-Windows GOOS treats '\' as an escape
		// metacharacter, and '[' '/' ']' as a character class — a library
		// path containing a literal backslash (e.g. a Windows-style path
		// surfaced while cross-testing under WSL) OR containing '[' ']'
		// (e.g. a user-chosen library folder name like "lib [beta] one")
		// silently makes Glob match nothing, no error raised. Python's
		// pathlib.Path.glob has no such trap: it only pattern-matches the
		// final path component, never earlier directory segments. A
		// plain directory listing sidesteps the whole class of bug and
		// needs no separate os.Stat "is it a directory" pre-check either
		// — a ReadDir failure is handled by the single warn-and-skip
		// branch below (which does distinguish "missing/not a directory"
		// from "permission denied" in the warning text, since those
		// call for different operator action).
		entries, readErr := os.ReadDir(steamappsDir)
		if readErr != nil {
			if errors.Is(readErr, fs.ErrPermission) {
				warnings = append(warnings, Warning{fmt.Sprintf(
					"library path %s: permission denied reading steamapps directory, skipping", lib)})
			} else {
				warnings = append(warnings, Warning{fmt.Sprintf(
					"library path %s has no steamapps directory, skipping", lib)})
			}
			continue
		}

		// Case-insensitive "appmanifest_*.acf" match: real Windows
		// production is the primary target (ADR-0005), and Windows
		// filesystems are case-insensitive — a manifest legitimately
		// named e.g. "AppManifest_100.ACF" (some third-party tool or a
		// manual copy/rename) must still be found. Python's reference
		// implementation gets this for free from Path.glob, which is
		// case-insensitive on Windows automatically; a Go string
		// prefix/suffix check is not, so it's done explicitly here.
		var manifestPaths []string
		for _, entry := range entries {
			name := strings.ToLower(entry.Name())
			if strings.HasPrefix(name, "appmanifest_") && strings.HasSuffix(name, ".acf") {
				manifestPaths = append(manifestPaths, filepath.Join(steamappsDir, entry.Name()))
			}
		}
		sort.Strings(manifestPaths)

		for _, manifestPath := range manifestPaths {
			app, parseErr := ParseAppManifestFile(manifestPath, lib)
			if parseErr != nil {
				warnings = append(warnings, Warning{fmt.Sprintf(
					"skipping corrupt manifest %s: %s", manifestPath, parseErr)})
				continue
			}

			if firstLib, dup := seenAppIDs[app.AppID]; dup {
				warnings = append(warnings, Warning{fmt.Sprintf(
					"duplicate appid %s in library %s, keeping first occurrence from %s",
					app.AppID, lib, firstLib)})
				continue
			}

			seenAppIDs[app.AppID] = lib
			apps = append(apps, app)
		}
	}

	return apps, warnings
}
