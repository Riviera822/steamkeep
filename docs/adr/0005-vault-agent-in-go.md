# ADR-0005: vault-agent ships as a Go binary

Date: 2026-08-05
Status: Accepted (user decision)

## Context

The plan left the agent language open ("Small Python or Go binary"). The
agent runs on end-user gaming machines — Windows PCs, Linux desktops, and
SteamOS devices (Steam Deck / Steam Machine, ARM64) per ADR-0002. Python
means either a runtime install (adoption hurdle) or PyInstaller bundles
(large, and notorious for antivirus false positives on exactly the target
audience's machines). Go produces a single static binary per platform via
trivial cross-compilation, and Go competence enters the repo in Phase 4
anyway (tsnet gomobile module).

## Decision

1. vault-agent ships in Go: one static binary per target
   (windows/amd64, linux/amd64, linux/arm64), no runtime dependencies.
2. The Phase-2 WP 2.1 Python parser is kept as the executable
   specification: its synthetic fixture corpus and test semantics define
   the KeyValues/ACF/VDF behavior; WP 2.1b ports parser + tests to Go
   against the same fixtures, then Python agent code is removed. All
   subsequent agent packages (reporter, hosts mode, Linux variant) are
   built in Go directly.
3. Build tooling: Go toolchain in WSL2, cross-compiled artifacts; CI
   builds land with Phase 5.
