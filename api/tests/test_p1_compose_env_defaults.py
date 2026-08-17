"""Packaging work package (WP P1) regression guard, reviewer should-fix S-D.

`deploy/compose.yaml` hand-copies vault-api's default values into
`${VAR:-default}` passthrough lines so a `docker compose up` with no `.env`
override still boots with the same defaults `vault_api/config.py` would pick
on its own. That is two independent sources of truth for the same number,
and `docs/LEARNINGS.md` ("Containers") already names the failure direction
that bites: a change to a `DEFAULT_*` constant in `config.py` alone leaves
`compose.yaml` quietly stale, and nothing catches it — the container still
starts, `docker compose config` still renders a value, it is just the WRONG
one, and it only ever surfaces as a confusing support report from an
operator who never touched `.env`.

This test derives the expected values FROM `config.py`'s own `DEFAULT_*`
constants (never hand-copied here) and compares them against
`deploy/compose.yaml`'s own `${VAR:-default}` text, parsed directly rather
than run through `docker compose config` — this file has no Docker
dependency and runs in plain `pytest`, in CI, on every commit; the
Docker-dependent cross-check lives in `deploy/tests/verify-stack.sh` step 3e
and its B1-audit extension, which need a real Docker host and a real
`docker compose config` render and therefore cannot run here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vault_api import config

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "deploy" / "compose.yaml"

# One entry per `${VAR:-default}` line in deploy/compose.yaml's vault-api
# `environment:` block that has a real default (VAULT_API_KEY uses `:?...`,
# no default, and is deliberately excluded). The expected value is always a
# real config.py symbol, never a literal typed here twice, EXCEPT the two
# noted cases where config.py itself has no named constant for the default
# (a literal there is the ground truth, not a copy of one).
EXPECTED_DEFAULTS: dict[str, str] = {
    "VAULT_LOG_LEVEL": "INFO",  # config.py: from_env()'s os.environ.get fallback (Settings.log_level has no dataclass default, and a from_env fallback is not introspectable — literal is defensible here)
    "VAULT_PREFILL_TIMEOUT_SECONDS": str(config.DEFAULT_PREFILL_TIMEOUT_SECONDS),
    "VAULT_WORKER_POLL_SECONDS": str(config.DEFAULT_WORKER_POLL_SECONDS),
    "VAULT_SIZE_CACHE_TTL": str(config.DEFAULT_SIZE_CACHE_TTL_SECONDS),
    "VAULT_AGENT_REPORT_KEEP": str(config.DEFAULT_AGENT_REPORT_KEEP),
    "VAULT_MANIFEST_KEEP": str(config.DEFAULT_MANIFEST_KEEP),
    "VAULT_GC_GRACE_DAYS": str(config.DEFAULT_GC_GRACE_DAYS),
    "VAULT_AUTO_GC": str(config.DEFAULT_AUTO_GC),
    "VAULT_EVENT_LOG_PATH": "",  # config.py: Settings.event_log_path dataclass default "", feature-off sentinel
    "VAULT_EVENT_SWEEP_INTERVAL_MINUTES": str(config.DEFAULT_EVENT_SWEEP_INTERVAL_MINUTES),
    "VAULT_MISS_TRIGGER_COOLDOWN_MINUTES": str(config.DEFAULT_MISS_TRIGGER_COOLDOWN_MINUTES),
    "VAULT_MISS_TRIGGER_MAX_PER_SWEEP": str(config.DEFAULT_MISS_TRIGGER_MAX_PER_SWEEP),
    "VAULT_BYPASS_WINDOW_DAYS": str(config.DEFAULT_BYPASS_WINDOW_DAYS),
    "VAULT_CLIENT_STATS_KEEP": str(config.DEFAULT_CLIENT_STATS_KEEP),
    "VAULT_EVENT_LOG_MAX_BYTES": str(config.DEFAULT_EVENT_LOG_MAX_BYTES),
    "VAULT_MANIFEST_ORACLE": config.MANIFEST_ORACLE_OFF,  # "" -- off by default, no DEFAULT_MANIFEST_ORACLE constant
    "VAULT_MANIFEST_ORACLE_URL": config.DEFAULT_MANIFEST_ORACLE_URL,
    "VAULT_MANIFEST_ORACLE_TIMEOUT": str(config.DEFAULT_MANIFEST_ORACLE_TIMEOUT),
    "VAULT_WEBHOOK_TIMEOUT_SECONDS": str(config.DEFAULT_WEBHOOK_TIMEOUT_SECONDS),
}


def _extract_vault_api_environment_block(compose_text: str) -> str:
    """Return the raw text of the `vault-api:` service's `environment:`
    block, isolated the same way `deploy/tests/verify-stack.sh` isolates a
    service block (its own comments explain why: stop at the next sibling
    service key OR the next top-level key, whichever comes first, so the
    block never runs past this one service even if it is rendered last).
    """
    lines = compose_text.splitlines()
    block: list[str] = []
    in_service = False
    in_environment = False
    for line in lines:
        if re.match(r"^  vault-api:\s*$", line):
            in_service = True
            continue
        if in_service and re.match(r"^  [A-Za-z0-9_-]+:\s*$", line):
            break  # next sibling service
        if in_service and re.match(r"^[A-Za-z]", line):
            break  # next top-level key (e.g. `volumes:`)
        if not in_service:
            continue
        if re.match(r"^\s{4}environment:\s*$", line):
            in_environment = True
            continue
        if in_environment:
            if re.match(r"^\s{4}[A-Za-z0-9_-]+:\s*$", line) or re.match(r"^\s{2,4}\S", line) and not re.match(
                r"^\s{6}", line
            ):
                # A line at the `environment:` block's own indent (4 spaces)
                # or shallower that ISN'T itself an env entry (env entries
                # sit at 6 spaces) ends the block.
                if not re.match(r"^\s{6}", line):
                    in_environment = False
                    continue
            block.append(line)
    return "\n".join(block)


def _parsed_env_defaults(block_text: str) -> dict[str, str]:
    """Parse every `KEY: ${VAR:-default}` line in ``block_text`` into a
    ``{KEY: default}`` dict. Deliberately narrow: only matches the exact
    `${VAR:-default}` passthrough shape every default-carrying line in this
    file already uses (see the neighbouring comments in compose.yaml for why
    that is the house style) -- a line using `:?` (required, no default,
    e.g. VAULT_API_KEY) does not match and is correctly absent from the
    result, not misread as an empty default.
    """
    found: dict[str, str] = {}
    pattern = re.compile(r"^\s{6}([A-Z0-9_]+):\s*\$\{[A-Z0-9_]+:-([^}]*)\}\s*$")
    for line in block_text.splitlines():
        m = pattern.match(line)
        if m:
            key, default = m.group(1), m.group(2)
            found[key] = default
    return found


@pytest.fixture(scope="module")
def compose_env_defaults() -> dict[str, str]:
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    block = _extract_vault_api_environment_block(text)
    assert block, (
        "Could not isolate vault-api's environment: block from "
        f"{COMPOSE_PATH} -- the extraction regex and the file's actual "
        "layout have drifted apart; fix the regex above, this is a test "
        "bug, not a real finding, if the file itself still parses as valid "
        "YAML."
    )
    return _parsed_env_defaults(block)


def test_every_expected_key_is_present_in_compose(compose_env_defaults: dict[str, str]) -> None:
    """Precondition before comparing values (LEARNINGS.md "Testing
    discipline": a value-only assertion passes happily on the empty string
    an accidentally-deleted passthrough line would also produce) -- if a
    key vanishes from compose.yaml entirely, THIS is what fails, not a
    same-looking value mismatch below.
    """
    missing = sorted(set(EXPECTED_DEFAULTS) - set(compose_env_defaults))
    assert not missing, (
        f"deploy/compose.yaml no longer forwards: {missing} -- either restore "
        "the passthrough line(s) or remove the key from EXPECTED_DEFAULTS "
        "here with a comment explaining why it is intentionally gone."
    )


@pytest.mark.parametrize("env_var", sorted(EXPECTED_DEFAULTS))
def test_compose_default_matches_config_default(env_var: str, compose_env_defaults: dict[str, str]) -> None:
    expected = EXPECTED_DEFAULTS[env_var]
    actual = compose_env_defaults.get(env_var)
    assert actual == expected, (
        f"deploy/compose.yaml's default for {env_var} is {actual!r}, but "
        f"vault_api/config.py's default is {expected!r} -- one of the two "
        "changed without the other. Update whichever one is wrong; do not "
        "edit EXPECTED_DEFAULTS to make this pass unless config.py's "
        "default is the one that changed on purpose."
    )


def test_no_unexpected_forwarded_keys_are_silently_untested(compose_env_defaults: dict[str, str]) -> None:
    """The inverse precondition: a NEW `${VAR:-default}` line added to
    compose.yaml's vault-api block in the future should be added to
    EXPECTED_DEFAULTS too, not silently pass this file by. VAULT_API_KEY is
    the one legitimate exception (`:?`, no default, never appears in
    ``compose_env_defaults`` at all -- see `_parsed_env_defaults`'s
    docstring).
    """
    extra = sorted(set(compose_env_defaults) - set(EXPECTED_DEFAULTS))
    assert not extra, (
        f"deploy/compose.yaml forwards {extra} with a default this test "
        "does not know about -- add it to EXPECTED_DEFAULTS (deriving the "
        "expected value from a config.py constant, not a hand-typed "
        "literal) so a future config.py default change is actually caught."
    )
