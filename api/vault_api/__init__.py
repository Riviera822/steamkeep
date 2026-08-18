"""vault-api — the FastAPI backend for SteamVault (depot mapping, prefill orchestration, cache control)."""

#: The ONE source of truth, IN CODE, for "what code is running" (WP 4e.7).
#: Hand-maintained — there are no release tags yet (plan §7 Phase 5, WP 5.5
#: unstarted) and the image is built from a checkout with no ``.git`` inside
#: it, so this cannot be computed from git describe/tags at import time or
#: read from the environment (an env var would just move the "two places
#: that can disagree" problem rather than remove it). Two Python consumers
#: reconcile with THIS value rather than carrying their own copy:
#: ``vault_api/main.py``'s ``FastAPI(version=...)`` and
#: ``GET /v1/settings``'s ``server_version`` field
#: (``vault_api/routers/settings.py``).
#:
#: This is NOT the only hand-maintained copy of the release number in the
#: repository — a build-time Dockerfile ``LABEL`` cannot read a Python
#: module, so it never can be. ``api/tests/test_version_pin.py``'s module
#: docstring carries the full, current table of every such site, which
#: ones it pins (bump either side alone and a named test fails), and which
#: ones are explicitly out of this package's footprint and therefore still
#: an unpinned, hand-checked gap — read that table before assuming "bump
#: this and one other file" is the complete release checklist; it is not.
__version__ = "0.1.0"
