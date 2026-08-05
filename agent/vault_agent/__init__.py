"""vault-agent: the deliberately dumb PC listener for SteamVault.

Reads Steam's local library metadata (appmanifest_*.acf, libraryfolders.vdf)
and reports installed app IDs to vault-api. Read + report only — see
docs/PROJECT_PLAN.md section 3 and ADR-0002.
"""

from __future__ import annotations
