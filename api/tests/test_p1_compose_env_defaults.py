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

**WP S-2 (ADR-0012) extension.** This file used to check only the
`vault-api:` service block. `deploy/compose.yaml` now also has a
`vault-runner:` service (the split-off SteamPrefill runner), and several
`VAULT_*` variables are forwarded on BOTH services with the SAME variable
name — sometimes with the SAME default for a genuinely shared setting
(`VAULT_PREFILL_TIMEOUT_SECONDS`, `VAULT_PREFILL_MODE`), sometimes present on
only ONE side because only that process's code actually reads it
(`VAULT_RUNNER_LEASE_TIMEOUT_SECONDS` is vault-api-only;
`VAULT_RUNNER_HEARTBEAT_SECONDS`/`VAULT_RUNNER_POLL_SECONDS` are
vault-runner-only — see `deploy/compose.yaml`'s own comments on each key for
the code-reference evidence). The extraction and comparison below are now
parametrized per SERVICE, each with its own expected-defaults table, so a
regression on either service's block is caught independently and a missing
key is attributed to the right one.
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
# real config.py symbol, never a literal typed here twice, EXCEPT the noted
# cases where config.py itself has no named constant for the default, OR
# (new in WP S-2) where compose's default is a DELIBERATE divergence from
# config.py's own default rather than a copy of it.
EXPECTED_DEFAULTS_VAULT_API: dict[str, str] = {
    "VAULT_LOG_LEVEL": "INFO",  # config.py: from_env()'s os.environ.get fallback (Settings.log_level has no dataclass default, and a from_env fallback is not introspectable — literal is defensible here)
    "VAULT_PREFILL_TIMEOUT_SECONDS": str(config.DEFAULT_PREFILL_TIMEOUT_SECONDS),
    "VAULT_WORKER_POLL_SECONDS": str(config.DEFAULT_WORKER_POLL_SECONDS),
    # WP S-2 (ADR-0012). Deliberately the literal "queue", NOT
    # config.PREFILL_MODE_SUBPROCESS (config.py's own built-in default) —
    # this compose file ships queue mode by design (deploy/compose.yaml's
    # own comment on this key has the full argument), while the bare-metal
    # dev path in api/README.md relies on config.py's default staying
    # 'subprocess' when the variable is never set at all. See
    # test_config_py_own_prefill_mode_default_is_still_subprocess below for
    # the pin on the OTHER half of that divergence — if config.py's default
    # ever changes, that test (not this dict) is what should catch it.
    "VAULT_PREFILL_MODE": "queue",
    # WP S-2. vault-api-only (worker.py's queue-mode wait loop is the ONLY
    # reader — prefill_runner.py never references this setting; see
    # deploy/compose.yaml's comment on this key for the grep evidence).
    "VAULT_RUNNER_LEASE_TIMEOUT_SECONDS": str(config.DEFAULT_RUNNER_LEASE_TIMEOUT_SECONDS),
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
    # WP 4h.0 (ADR-0010): env-only privacy gate, forwarded directly (unlike
    # VAULT_SWEEP_INCLUDE_CACHED, which is deliberately absent from this dict
    # because compose never forwards it -- it is DB-overridable at runtime
    # via PATCH /v1/settings instead, ADR-0009) -- see deploy/compose.yaml's
    # own comment for why that one is the exception.
    "VAULT_RELAY_EXPOSE_PLAYTIME": "true" if config.DEFAULT_RELAY_EXPOSE_PLAYTIME else "false",
    "VAULT_RELAY_EXPOSE_LAST_PLAYED": (
        "true" if config.DEFAULT_RELAY_EXPOSE_LAST_PLAYED else "false"
    ),
    # Fable-audit follow-up (security lock that fails open if omitted):
    # VAULT_SETTINGS_READONLY is env-only BY DEFINITION (ADR-0009 decision 3
    # -- a lock on the settings-write API cannot be unlockable through that
    # same API), so unlike VAULT_SWEEP_INCLUDE_CACHED above it has NO
    # DB-overridable alternative path at all and forwarding it here is the
    # only way an operator following api/README.md's "the operator
    # hard-lock" can ever reach it. No DEFAULT_SETTINGS_READONLY constant
    # exists in config.py (Settings.from_env inlines the literal `False`
    # default for `_env_bool("VAULT_SETTINGS_READONLY", False)`) -- the
    # applicable precedent is VAULT_EVENT_LOG_PATH above (no derivable
    # env-default symbol either), NOT VAULT_LOG_LEVEL: that one's stated
    # rationale is "no dataclass default", which does not hold here --
    # `settings_readonly: bool = False` is a real dataclass default
    # (config.py:923), just not one that is itself named. Not a coverage
    # hole either way: the env default (`False` for unset/blank) is already
    # pinned by three tests in test_config.py --
    # test_settings_readonly_defaults_to_false, the blank-string case of
    # test_settings_readonly_accepts_false_spellings (falsy="", which falls
    # through to the default rather than parsing anything), and
    # test_env_bool_error_names_the_original_unstripped_value_and_the_default
    # (pins the default's own value inside its error-message assertion) --
    # NOT the true-spellings/rejects-anything-else siblings, which set the
    # env var explicitly and would not notice a default change.
    "VAULT_SETTINGS_READONLY": "false",
}

# WP S-2 (ADR-0012). The runner's own `environment:` block. Deliberately
# narrow: only the four variables `vault_api/prefill_runner.py` (or the
# `Settings.from_env` loader it shares with vault-api) actually reads —
# every job-lifecycle setting (scheduler, auto-GC, webhooks, the Steam relay
# gate, the settings hard-lock, ...) lives only in EXPECTED_DEFAULTS_VAULT_API
# above, because prefill_runner.py imports nothing from worker.py and never
# reads any of them (ADR-0012 §1). See deploy/compose.yaml's comment block on
# the vault-runner service for the per-key evidence of which side reads what.
EXPECTED_DEFAULTS_VAULT_RUNNER: dict[str, str] = {
    "VAULT_LOG_LEVEL": "INFO",  # same literal/reasoning as vault-api's copy above
    "VAULT_PREFILL_MODE": "queue",  # same deliberate-divergence literal as vault-api's copy above
    "VAULT_PREFILL_TIMEOUT_SECONDS": str(config.DEFAULT_PREFILL_TIMEOUT_SECONDS),
    "VAULT_RUNNER_HEARTBEAT_SECONDS": str(config.DEFAULT_RUNNER_HEARTBEAT_SECONDS),
    "VAULT_RUNNER_POLL_SECONDS": str(config.DEFAULT_RUNNER_POLL_SECONDS),
}

#: One row per Compose service this file checks, in the sense used
#: throughout: 'the exact set of `${VAR:-default}` keys this service's
#: `environment:` block should forward, and what each one's default should
#: be'. Adding a new checked service means adding one entry here and nothing
#: else — every test function below is parametrized over this mapping.
SERVICE_EXPECTED_DEFAULTS: dict[str, dict[str, str]] = {
    "vault-api": EXPECTED_DEFAULTS_VAULT_API,
    "vault-runner": EXPECTED_DEFAULTS_VAULT_RUNNER,
}


def _extract_service_environment_block(compose_text: str, service_name: str) -> str:
    """Return the raw text of ``service_name``'s `environment:` block,
    isolated the same way `deploy/tests/verify-stack.sh` isolates a service
    block (its own comments explain why: stop at the next sibling service
    key OR the next top-level key, whichever comes first, so the block never
    runs past this one service even if it is rendered last).

    Generalized from a vault-api-only helper (WP S-2, ADR-0012) so the same
    logic serves any service name — the extraction itself has nothing
    vault-api-specific about it.
    """
    lines = compose_text.splitlines()
    block: list[str] = []
    in_service = False
    in_environment = False
    header = re.compile(rf"^  {re.escape(service_name)}:\s*$")
    for line in lines:
        if header.match(line):
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


def _extract_service_volumes_block(compose_text: str, service_name: str) -> str:
    """Sibling of `_extract_service_environment_block` above, isolating a
    service's `volumes:` sub-block instead of its `environment:` one — same
    service-block isolation logic (stop at the next sibling service key or
    the next top-level key), just keyed on a different inner section name.

    Review round 2, should-fix S1: the shared-HOME volume invariant
    (`/opt/steamprefill/home` mounted on BOTH vault-api and vault-runner —
    see deploy/compose.yaml's long comment on that mount for why: manifest
    ingestion stays vault-api-side and can only read what SteamPrefill,
    running in vault-runner, writes there if both share the same volume) had
    no CI-runnable pin — only `deploy/tests/verify-stack.sh`'s live
    cross-container write/read proof, which needs a real Docker host and is
    not wired into `ci.yml`. This is that pin's plumbing.
    """
    lines = compose_text.splitlines()
    block: list[str] = []
    in_service = False
    in_volumes = False
    header = re.compile(rf"^  {re.escape(service_name)}:\s*$")
    for line in lines:
        if header.match(line):
            in_service = True
            continue
        if in_service and re.match(r"^  [A-Za-z0-9_-]+:\s*$", line):
            break  # next sibling service
        if in_service and re.match(r"^[A-Za-z]", line):
            break  # next top-level key (e.g. `volumes:`)
        if not in_service:
            continue
        if re.match(r"^\s{4}volumes:\s*$", line):
            in_volumes = True
            continue
        if in_volumes:
            # Round 3 nitpick: simplified to the one condition that actually
            # matters. Every real volumes: entry (and every comment line
            # belonging to it) sits at exactly 6-space indent
            # ("      - name:/path" / "      # ..."); anything shallower —
            # the block's own 4-space indent (`security_opt:`, ...), a
            # sibling top-level key, or a blank line — ends the block. The
            # earlier two-condition version computed this same check twice
            # (once in the outer `or`, again in the redundant inner `if`).
            if not re.match(r"^\s{6}", line):
                in_volumes = False
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
def compose_text() -> str:
    return COMPOSE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def env_defaults_by_service(compose_text: str) -> dict[str, dict[str, str]]:
    """`{service_name: {VAR: default}}` for every service in
    ``SERVICE_EXPECTED_DEFAULTS`` — computed once per test-module run, all
    three test functions below reuse the same parse.
    """
    result: dict[str, dict[str, str]] = {}
    for service_name in SERVICE_EXPECTED_DEFAULTS:
        block = _extract_service_environment_block(compose_text, service_name)
        assert block, (
            f"Could not isolate {service_name}'s environment: block from "
            f"{COMPOSE_PATH}. Three possibilities (review round 2 nitpick): "
            "(1) the extraction regex and the file's actual layout have "
            "drifted apart -- a test bug, not a real finding, if the file "
            "itself still parses as valid YAML; (2) the service was "
            "deliberately removed from compose.yaml, in which case remove "
            "it from SERVICE_EXPECTED_DEFAULTS too rather than fixing the "
            "regex; (3) the service was dropped or renamed by accident -- a "
            "real finding this assertion exists to catch (mutation-tested "
            "during WP S-2 by renaming vault-runner's own service key)."
        )
        result[service_name] = _parsed_env_defaults(block)
    return result


@pytest.mark.parametrize("service_name", sorted(SERVICE_EXPECTED_DEFAULTS))
def test_every_expected_key_is_present_in_compose(
    service_name: str, env_defaults_by_service: dict[str, dict[str, str]]
) -> None:
    """Precondition before comparing values (LEARNINGS.md "Testing
    discipline": a value-only assertion passes happily on the empty string
    an accidentally-deleted passthrough line would also produce) -- if a
    key vanishes from a service's block entirely, THIS is what fails, not a
    same-looking value mismatch below.
    """
    expected = SERVICE_EXPECTED_DEFAULTS[service_name]
    actual = env_defaults_by_service[service_name]
    missing = sorted(set(expected) - set(actual))
    assert not missing, (
        f"deploy/compose.yaml's {service_name}: service no longer forwards: "
        f"{missing} -- either restore the passthrough line(s) or remove the "
        f"key from SERVICE_EXPECTED_DEFAULTS[{service_name!r}] here with a "
        "comment explaining why it is intentionally gone."
    )


# One (service, env_var) pair per expected key across every checked service --
# flattened so a failure names exactly which service AND which variable broke,
# rather than only the variable name (which, for VAULT_PREFILL_MODE and
# VAULT_PREFILL_TIMEOUT_SECONDS, appears in BOTH services' tables with
# possibly-independent expected values).
_ALL_SERVICE_VAR_PAIRS: list[tuple[str, str]] = sorted(
    (service_name, env_var)
    for service_name, expected in SERVICE_EXPECTED_DEFAULTS.items()
    for env_var in expected
)


@pytest.mark.parametrize("service_name,env_var", _ALL_SERVICE_VAR_PAIRS)
def test_compose_default_matches_config_default(
    service_name: str, env_var: str, env_defaults_by_service: dict[str, dict[str, str]]
) -> None:
    expected = SERVICE_EXPECTED_DEFAULTS[service_name][env_var]
    actual = env_defaults_by_service[service_name].get(env_var)
    assert actual == expected, (
        f"deploy/compose.yaml's {service_name}: default for {env_var} is "
        f"{actual!r}, but the expected value is {expected!r} -- one of the "
        "two changed without the other. Update whichever one is wrong; do "
        "not edit SERVICE_EXPECTED_DEFAULTS to make this pass unless the "
        "change is deliberate (and if it involves VAULT_PREFILL_MODE, update "
        "this file's comment explaining the divergence from config.py's own "
        "default too)."
    )


@pytest.mark.parametrize("service_name", sorted(SERVICE_EXPECTED_DEFAULTS))
def test_no_unexpected_forwarded_keys_are_silently_untested(
    service_name: str, env_defaults_by_service: dict[str, dict[str, str]]
) -> None:
    """The inverse precondition: a NEW `${VAR:-default}` line added to a
    service's block in the future should be added to
    SERVICE_EXPECTED_DEFAULTS too, not silently pass this file by.
    VAULT_API_KEY is the one legitimate exception (`:?`, no default, never
    appears in ``env_defaults_by_service`` at all -- see
    `_parsed_env_defaults`'s docstring).
    """
    expected = SERVICE_EXPECTED_DEFAULTS[service_name]
    actual = env_defaults_by_service[service_name]
    extra = sorted(set(actual) - set(expected))
    assert not extra, (
        f"deploy/compose.yaml's {service_name}: service forwards {extra} "
        "with a default this test does not know about -- add it to "
        f"SERVICE_EXPECTED_DEFAULTS[{service_name!r}] (deriving the expected "
        "value from a config.py constant, not a hand-typed literal, unless "
        "it is a deliberate divergence like VAULT_PREFILL_MODE's) so a "
        "future default change is actually caught."
    )


def test_config_py_own_prefill_mode_default_is_still_subprocess() -> None:
    """Pins the OTHER half of the VAULT_PREFILL_MODE divergence above.

    `deploy/compose.yaml` ships `VAULT_PREFILL_MODE=queue` on both services
    (deliberately, see the two dicts above) — but that is only a safe
    divergence to hand-type as a literal here for as long as `config.py`'s
    OWN built-in default (what applies when the variable is unset entirely —
    the bare-metal/native dev path in api/README.md's "Quickstart") stays
    'subprocess'. If that constant ever changes, the "queue" literals above
    stop documenting a deliberate choice and start hiding a real default
    change; this test is what would actually notice, since neither dict
    above derives its "queue" value FROM config.py (by design — see their
    own comments).
    """
    assert config.PREFILL_MODE_SUBPROCESS == "subprocess"
    settings = config.Settings(
        vault_api_key="test-key", db_path=":memory:", cache_root="./cache", log_level="INFO"
    )
    assert settings.prefill_mode == config.PREFILL_MODE_SUBPROCESS


# WP S-2's own completeness discipline: every NEW `VAULT_*` variable this
# package's compose wiring introduces must have an operator-facing stanza in
# `deploy/.env.example`, not just a `${VAR:-default}` passthrough line in
# `compose.yaml` — docs/LEARNINGS.md "Containers" names the general failure
# shape this guards against (a doc sentence telling an operator to set a
# variable is a claim the shipped stack forwards it, AND the reverse: a
# variable the stack forwards needs a doc sentence an operator can find it
# by). Deliberately narrow to the variables THIS work package introduced —
# not a general audit of every pre-existing key deploy/.env.example already
# documents (that ground is `deploy/tests/verify-stack.sh` step 3e/B1's, via
# a real `docker compose config` render).
ENV_EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "deploy" / ".env.example"

#: New in WP S-2. Same set as the union of SERVICE_EXPECTED_DEFAULTS' keys
#: above, minus VAULT_LOG_LEVEL and VAULT_PREFILL_TIMEOUT_SECONDS (both
#: pre-existing variables, already documented in `.env.example` before this
#: work package — only forwarding a SECOND service with them is new, not the
#: variable itself, so they are not re-asserted here).
NEW_WP_S2_ENV_VARS = (
    "VAULT_PREFILL_MODE",
    "VAULT_RUNNER_LEASE_TIMEOUT_SECONDS",
    "VAULT_RUNNER_HEARTBEAT_SECONDS",
    "VAULT_RUNNER_POLL_SECONDS",
)


@pytest.mark.parametrize("env_var", NEW_WP_S2_ENV_VARS)
def test_new_wp_s2_vars_are_documented_in_env_example(env_var: str) -> None:
    """Mutation target: delete any one of this WP's `.env.example` stanzas
    (leaving the compose.yaml passthrough line untouched) and this is the
    test that dies — the operator-facing half of the completeness discipline
    that `test_every_expected_key_is_present_in_compose` above enforces for
    the compose.yaml half.

    Deliberately anchored to the actual ASSIGNMENT-line shape
    (`#?VAR=value`, `.env.example`'s own convention: commented-out for a
    documented default, uncommented for a live override) rather than a bare
    substring search — a prose comment merely MENTIONING the variable name
    (e.g. "vault-runner writes the heartbeat on its own cadence
    (VAULT_RUNNER_HEARTBEAT_SECONDS below)") must not satisfy this check on
    its own once the actual stanza below it is gone; measured directly
    (mutation run during this WP: deleting only the `#VAULT_RUNNER_
    HEARTBEAT_SECONDS=5.0` line left a passing false-positive under a bare
    `env_var in text` substring check, because a neighbouring cross-reference
    comment still named the variable — this regex is the fix for that).
    """
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    pattern = re.compile(rf"^#?{re.escape(env_var)}=", re.MULTILINE)
    assert pattern.search(text), (
        f"deploy/.env.example has no {env_var}=... or #{env_var}=... "
        "assignment line, but deploy/compose.yaml forwards it (see "
        "SERVICE_EXPECTED_DEFAULTS above) -- an operator has no way to "
        "discover this variable exists from a bare mention in a neighbouring "
        "comment alone. Restore its documentation stanza in "
        "deploy/.env.example."
    )


# ==========================================================================
# Review round 2, should-fix S1: a CI-runnable pin for the shared-HOME
# volume invariant. Silent-forever failure mode if this regresses: manifest
# ingestion (vault_api/manifest_ingest.py, still vault-api-side per
# ADR-0012 §1) would find nothing to ingest on every queue-mode prefill,
# with no error -- `IngestResult.cache_dir_unavailable` is explicitly
# documented as "not an error". `deploy/tests/verify-stack.sh` step 6k
# already proves this LIVE (a file written from vault-runner is read back
# from vault-api), but that check needs a real Docker host and is not part
# of `ci.yml` -- this is the static, always-runs-in-CI counterpart.
# ==========================================================================


#: Anchors a volume-mount check to the actual YAML sequence-item SHAPE
#: (`      - <source>:<target>`), never a bare substring search. Round 3
#: correction: the extracted blocks this matches against are mostly
#: COMMENTS, not mount lines, and those comments routinely mention the very
#: paths this file checks for in prose -- measured directly, not
#: hypothetically: vault-runner's own volumes: block comment (S3's
#: blast-radius explanation) contains the literal substring "/vault" twice,
#: in lines like "# ${VAULT_CACHE_PATH:-vault-cache}:/vault, the depot
#: cache ..." -- a bare `"/vault" in runner_volumes` check would read that
#: comment as if it were a real mount and never notice the real mount's
#: removal. Same class of bug this project's OWN round-1 anchoring lesson
#: already names for a different file (`.env.example` stanza vs. a
#: cross-reference comment naming the same variable) -- one file over here.
def _has_volume_mount(block_text: str, target_path: str) -> bool:
    pattern = re.compile(
        rf"^[ \t]*-[ \t]*\S+:{re.escape(target_path)}[ \t]*$", re.MULTILINE
    )
    return pattern.search(block_text) is not None


def test_home_volume_is_shared_by_vault_api_and_vault_runner(compose_text: str) -> None:
    api_volumes = _extract_service_volumes_block(compose_text, "vault-api")
    runner_volumes = _extract_service_volumes_block(compose_text, "vault-runner")
    assert _has_volume_mount(api_volumes, "/opt/steamprefill/home"), (
        "deploy/compose.yaml's vault-api service no longer mounts "
        "/opt/steamprefill/home -- manifest ingestion (vault-api-side, "
        "ADR-0012 §1) would have nothing to read back."
    )
    assert _has_volume_mount(runner_volumes, "/opt/steamprefill/home"), (
        "deploy/compose.yaml's vault-runner service no longer mounts "
        "/opt/steamprefill/home -- SteamPrefill (running there) would write "
        "its manifest temp-cache into an ephemeral, non-shared location "
        "vault-api can never see."
    )


def test_config_volume_is_mounted_on_vault_runner_only(compose_text: str) -> None:
    """ADR-0012 §5: the Steam-session volume moved OFF vault-api entirely in
    WP S-2 (see deploy/compose.yaml's comment on vault-api's volumes: for
    the call-graph evidence: no queue-mode code path on vault-api still
    calls prefill.resolve_executable/run_prefill). Unlike
    /opt/steamprefill/home above, this one must NOT be shared.
    """
    api_volumes = _extract_service_volumes_block(compose_text, "vault-api")
    runner_volumes = _extract_service_volumes_block(compose_text, "vault-runner")
    assert not _has_volume_mount(api_volumes, "/opt/steamprefill/Config"), (
        "deploy/compose.yaml's vault-api service mounts "
        "/opt/steamprefill/Config again -- ADR-0012 §5 moved the "
        "credential-bearing Steam-session volume to vault-runner; a second "
        "copy on vault-api is a regression of that decision, not a harmless "
        "extra."
    )
    assert _has_volume_mount(runner_volumes, "/opt/steamprefill/Config"), (
        "deploy/compose.yaml's vault-runner service no longer mounts "
        "/opt/steamprefill/Config -- the one-time interactive SteamPrefill "
        "login (deploy/README.md \"First run\") would have nowhere "
        "persistent to store the Steam session."
    )
