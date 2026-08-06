package hostsfile

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
)

// Write methods reported in Result.Method.
const (
	MethodRename  = "rename"   // atomic: temp file in the same directory, then rename over the target
	MethodInPlace = "in-place" // non-atomic fallback: truncate + rewrite the existing file
)

// defaultFileMode is used only when the target file does not exist yet
// (the backup file, on its first write). An existing file's own mode is
// always preserved instead.
const defaultFileMode fs.FileMode = 0o644

// mutate performs the one write this package ever does: back up oldRaw,
// then replace the file's contents with newRaw.
//
// The backup is written FIRST and its failure aborts the whole operation
// (fail closed). "Reversibility above all" is worth more than the ability
// to apply a hosts entry on a machine where we cannot create a file next
// to the hosts file. Note this is NOT merely belt-and-braces for the case
// where the write would fail anyway: a directory can reject new files
// (so no .bak, no temp file) while the existing hosts file stays writable
// in place — without the abort, that configuration would mutate the file
// and leave the user no undo copy at all. Pinned by
// TestApply_RefusesWhenTheBackupCannotBeWritten.
//
// # TOCTOU, stated rather than papered over
//
// oldRaw is the content the caller read a moment ago, so the backup
// preserves what WE read, not necessarily what is on disk at the instant of
// the write. Nothing is locked, deliberately — a hosts file is edited by
// humans in text editors, and holding a lock on a file the whole machine
// resolves through would be more disruptive than the narrow race it closes.
// (A sibling O_CREATE|O_EXCL lockfile would exclude one vault-agent from
// another, but not the realistic writer — Notepad — while adding a
// stale-lock failure mode on a system file. Not worth it here: `hosts`
// commands are manual and one-shot, and `report` never touches this file.)
//
// The consequences differ by write path, and the in-place one is worse than
// "you lose a line":
//
//   - rename path: the loser of the race is overwritten wholesale. The
//     concurrent editor's version is simply replaced by ours.
//   - in-place path: we truncate and rewrite while the other writer is
//     mid-save, so the result can be genuinely CORRUPT — interleaved or
//     truncated content, not just a missing entry. Measured, not theorized.
//
// Corruption of that kind fails CLOSED on the next run rather than
// compounding: a damaged block trips the markers-corrupt check and every
// subsequent Apply/Remove refuses to touch the file until a human fixes it.
// And it is recoverable — <path>.steamvault.bak holds the pre-mutation
// bytes.
func mutate(path string, oldRaw, newRaw []byte) (backupPath, method string, err error) {
	mode := defaultFileMode
	if info, serr := os.Stat(path); serr == nil {
		mode = info.Mode().Perm()
	}

	backupPath = path + BackupSuffix
	if _, _, berr := writeFile(backupPath, oldRaw, mode); berr != nil {
		return "", "", fmt.Errorf(
			"refusing to modify %s: the backup %s could not be written (%w). "+
				"No change was made", path, backupPath, berr)
	}

	method, truncated, err := writeFile(path, newRaw, mode)
	if err != nil {
		return backupPath, "", writeFailureMessage(path, backupPath, truncated, err)
	}
	return backupPath, method, nil
}

// writeFailureMessage phrases a failed write honestly. Split out of mutate so
// both branches are directly testable: getting this wrong is not a cosmetic
// bug — telling a user their system hosts file is intact when the non-atomic
// fallback has already truncated it sends them away from the one action that
// fixes it.
func writeFailureMessage(path, backupPath string, truncated bool, err error) error {
	if truncated {
		// The in-place fallback emptied the file before failing (a full disk
		// is the realistic cause). Claiming the original is "preserved" here
		// would be a lie about a file the whole machine resolves through.
		return fmt.Errorf(
			"%s could not be written: %w\n"+
				"WARNING: the non-atomic in-place fallback had already truncated the file, so %s "+
				"may be partially written or empty right now — restore it by copying %s over it",
			path, err, path, backupPath)
	}
	return fmt.Errorf(
		"%s could not be written: %w\nThe file was NOT modified; a copy of it is at %s",
		path, err, backupPath)
}

// writeFile replaces path's contents with data.
//
// # Strategy, and the empirical evidence behind it (WP 2.3)
//
// PRIMARY: write a temp file in the SAME directory, fsync it, rename it
// over the target. The rename is atomic on both Linux and Windows
// (MoveFileEx with MOVEFILE_REPLACE_EXISTING), so a crash or a power cut
// mid-write can never leave a truncated hosts file — which on this
// particular file would break name resolution for the whole machine.
//
// FALLBACK: if anything in the temp+rename path fails, truncate the
// existing file and write into it directly. This is NOT atomic, which is
// exactly why it is second, not first — but it is reachable and necessary.
// Measured on real Windows 11 with a cross-compiled probe against
// ACL-restricted files (no admin rights needed to reproduce; the real
// hosts file was never touched):
//
//	file/directory ACL                                  os.Rename   in-place
//	------------------------------------------------------------------------
//	unrestricted                                        ok          ok
//	file denies DELETE (parent still grants delete-child) ok         ok
//	file denies DELETE + parent denies DELETE_CHILD      DENIED      ok
//	file denies FILE_WRITE_DATA                          ok          DENIED
//	both denied                                          DENIED      DENIED
//
// Neither strategy dominates the other. The third row is the shape a
// hardened hosts file actually takes (security software commonly blocks
// replacing or deleting it while still allowing an administrator to edit
// it) — without the fallback, hosts mode would simply be unavailable on
// those machines. Every failing case surfaced as fs.ErrPermission, so
// IsPermissionDenied below is reliable on Windows and not just on Unix.
//
// KNOWN CAVEAT (measured, not assumed): a rename REPLACES the file object,
// so any EXPLICIT (non-inherited) ACE on the old hosts file is lost and the
// new file inherits its ACL from %SystemRoot%\System32\drivers\etc. On a
// default Windows install the hosts file's ACL is entirely inherited from
// that directory anyway, so the resulting ACL is identical. And the ACLs
// that would actually be worth preserving — the hardening ones — deny
// DELETE, which pushes us onto the in-place path that preserves them by
// construction. Preserving an explicit ACL across a rename would require
// golang.org/x/sys/windows; this module is dependency-free by ADR-0005, so
// this is documented rather than implemented.
//
// The file's permission bits ARE preserved on both platforms: mode comes
// from the caller (stat of the original) and is applied to the temp file
// before the rename. Without that, os.CreateTemp's 0600 would land on
// /etc/hosts and make it unreadable to every non-root process on the
// machine — a much worse outcome than the change we set out to make.
// The second return value, truncated, reports whether the in-place fallback
// got as far as opening the target with O_TRUNC — i.e. whether the file on
// disk may now be empty or partially written. It is only ever true together
// with a non-nil error; callers use it to tell "nothing happened" apart from
// "the file needs restoring from the backup".
func writeFile(path string, data []byte, mode fs.FileMode) (method string, truncated bool, err error) {
	dir := filepath.Dir(path)

	renameErr := tryAtomicReplace(dir, path, data, mode)
	if renameErr == nil {
		return MethodRename, false, nil
	}

	f, oerr := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, mode)
	if oerr != nil {
		// The open failed, so O_TRUNC never took effect: the file is intact.
		return "", false, errors.Join(
			fmt.Errorf("atomic replace failed: %w", renameErr),
			fmt.Errorf("in-place fallback could not open the file: %w", oerr),
		)
	}
	if werr := writeSyncClose(f, data); werr != nil {
		// The open SUCCEEDED, so the file was emptied before this failure.
		return "", true, errors.Join(
			fmt.Errorf("atomic replace failed: %w", renameErr),
			fmt.Errorf("in-place fallback failed mid-write: %w", werr),
		)
	}
	return MethodInPlace, false, nil
}

// tryAtomicReplace is the temp+rename path. Any temp file it creates is
// always cleaned up on failure, so a failed apply never litters the hosts
// directory with .tmp files.
func tryAtomicReplace(dir, path string, data []byte, mode fs.FileMode) error {
	tmp, err := os.CreateTemp(dir, ".steamvault-hosts-*.tmp")
	if err != nil {
		return fmt.Errorf("could not create a temp file in %s: %w", dir, err)
	}
	tmpName := tmp.Name()

	// Chmod BEFORE the rename: after it, the file is live.
	if err := tmp.Chmod(mode); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return fmt.Errorf("could not set mode %04o on the temp file: %w", mode.Perm(), err)
	}
	if err := writeSyncClose(tmp, data); err != nil {
		os.Remove(tmpName)
		return fmt.Errorf("could not write the temp file: %w", err)
	}
	if err := os.Rename(tmpName, path); err != nil {
		os.Remove(tmpName)
		return fmt.Errorf("could not rename %s onto %s: %w", tmpName, path, err)
	}
	syncDir(dir)
	return nil
}

// writeSyncClose writes data, flushes it to stable storage, and closes f.
// f is closed on every path, including the error ones.
func writeSyncClose(f *os.File, data []byte) error {
	if _, err := f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

// syncDir best-effort fsyncs the directory so the rename itself is durable
// on Linux. Errors are ignored on purpose: on Windows a directory handle
// cannot be flushed this way, and the operation has already succeeded —
// failing the whole apply over a durability nicety would be wrong.
func syncDir(dir string) {
	d, err := os.Open(dir)
	if err != nil {
		return
	}
	_ = d.Sync()
	_ = d.Close()
}

// IsPermissionDenied reports whether err is (or wraps) a permission
// failure — the signal for the caller to print ElevationHint instead of a
// bare error. Verified to fire for Windows ERROR_ACCESS_DENIED on all
// three failing syscalls (rename, open-for-write, open-for-read) as well
// as for EACCES/EPERM on Linux.
func IsPermissionDenied(err error) bool {
	return errors.Is(err, fs.ErrPermission)
}
