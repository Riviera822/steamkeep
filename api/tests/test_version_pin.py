"""WP 4e.7 anti-drift guard: ``vault_api.__version__`` vs. every other
hand-maintained copy of the same release number this package's footprint
(``api/``, plus ``deploy/`` only where the pin requires it) can reach.

Same failure class docs/LEARNINGS.md already names for env-var defaults
(``test_p1_compose_env_defaults.py``): two hand-maintained copies of the same
fact, checked in two different files, will disagree eventually unless
something compares them on every commit. Here the "same fact" is one level
up from an env var — it is the released image tag / OCI version — but the
guard shape is identical: derive every side from its real source (never
hand-copy any of them into this file), parse the real files directly (no
Docker, no network, plain ``pytest``, runs in CI on every commit), and fail
BY NAME in either drift direction for each site.

**Every hand-maintained "0.1.0" this project has, and which ones this file
pins** (review round 1, blocker B1: an earlier version of this file covered
only ``deploy/compose.yaml`` and both this module's own docstring and
``api/README.md`` wrongly implied that was the complete set):

| Site | Pinned here? | Why |
|---|---|---|
| ``api/vault_api/__init__.py``'s ``__version__`` | n/a (the source of truth) | Everything else is compared AGAINST this |
| ``deploy/compose.yaml`` (3x ``image:`` lines) | YES | in this pin's footprint, checked at startup-config time |
| ``api/Dockerfile``'s ``org.opencontainers.image.version`` LABEL | YES | in ``api/``, this pin's footprint; a build-time literal that cannot read a Python module |
| ``deploy/tests/verify-stack.sh``'s ``TAG=${VAULT_IMAGE_TAG:-...}`` | YES | in ``deploy/``, same conceptual value as compose's default |
| ``deploy/.env.example``'s ``#VAULT_IMAGE_TAG=...`` example line | YES | in ``deploy/``, same conceptual value as compose's default |
| ``core/Dockerfile``'s ``org.opencontainers.image.version`` LABEL | **NO — out of this WP's footprint** | ``core/`` belongs to vault-core, a different component this package was not scoped to touch |
| ``dns/Dockerfile``'s ``org.opencontainers.image.version`` LABEL | **NO — out of this WP's footprint** | ``dns/`` belongs to vault-dns, same reasoning |

The two **NO** rows are a real, currently-unpinned gap, not an oversight this
file hides: a release bump must ALSO hand-edit those two LABELs, and nothing
in this repository's test suite catches a miss there today. Extending this
exact pattern into ``core/tests/`` and ``dns/`` (each component keeps its own
test suite) is the natural follow-up if/when a future work package owns
those directories; recorded here so the next reader finds this table instead
of rediscovering the gap.

Mutation-verified in both directions for every **YES** row above (bump
``vault_api.__version__`` alone; bump each site's own value alone) — each
failure is a DIFFERENT named test below, so a reader immediately knows which
side of which comparison drifted.
"""

from __future__ import annotations

import re
from pathlib import Path

from vault_api import __version__ as VAULT_API_VERSION
from vault_api.config import Settings
from vault_api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "deploy" / "compose.yaml"
API_DOCKERFILE_PATH = REPO_ROOT / "api" / "Dockerfile"
VERIFY_STACK_PATH = REPO_ROOT / "deploy" / "tests" / "verify-stack.sh"
ENV_EXAMPLE_PATH = REPO_ROOT / "deploy" / ".env.example"

#: Matches `image: steamvault/<service>:${VAULT_IMAGE_TAG:-<default>}` lines
#: exactly as they appear in deploy/compose.yaml today (vault-core, vault-api,
#: vault-dns) -- deliberately narrow, like
#: test_p1_compose_env_defaults.py's `_parsed_env_defaults` regex, so a line
#: that does not match this exact `${VAR:-default}` shape is correctly absent
#: rather than misparsed.
_IMAGE_TAG_PATTERN = re.compile(
    r"^\s*image:\s*steamvault/([a-z-]+):\$\{VAULT_IMAGE_TAG:-([^}]+)\}\s*$",
    re.MULTILINE,
)

#: Matches ONLY the real LABEL assignment (`="0.1.0"`) — deliberately
#: requires the `="..."` shape immediately after the key name, so a comment
#: that merely mentions `org.opencontainers.image.version` in prose (this
#: file's own module docstring above does, and so does api/Dockerfile's
#: explanatory comment next to the real LABEL) can never accidentally match.
_OCI_VERSION_PATTERN = re.compile(r'org\.opencontainers\.image\.version="([^"]+)"')

#: verify-stack.sh's `TAG=` line is bash, not YAML, but the same
#: `${VAR:-default}` shape compose.yaml uses -- one pattern, two files.
_VERIFY_STACK_TAG_PATTERN = re.compile(
    r"^\s*TAG=\$\{VAULT_IMAGE_TAG:-([^}]+)\}\s*$", re.MULTILINE
)

#: The commented-out example line in .env.example, e.g. `#VAULT_IMAGE_TAG=0.1.0`.
_ENV_EXAMPLE_TAG_PATTERN = re.compile(r"^#VAULT_IMAGE_TAG=(.+)$", re.MULTILINE)


def _compose_image_tag_defaults() -> dict[str, str]:
    """{service_name: default_tag} for every `image:` line in compose.yaml
    that falls back to a `${VAULT_IMAGE_TAG:-...}` default.
    """
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    return {service: default for service, default in _IMAGE_TAG_PATTERN.findall(text)}


def _dockerfile_oci_version() -> str | None:
    text = API_DOCKERFILE_PATH.read_text(encoding="utf-8")
    match = _OCI_VERSION_PATTERN.search(text)
    return match.group(1) if match else None


def _verify_stack_tag_default() -> str | None:
    text = VERIFY_STACK_PATH.read_text(encoding="utf-8")
    match = _VERIFY_STACK_TAG_PATTERN.search(text)
    return match.group(1) if match else None


def _env_example_tag_default() -> str | None:
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    match = _ENV_EXAMPLE_TAG_PATTERN.search(text)
    return match.group(1) if match else None


# ==========================================================================
# Preconditions -- same discipline as
# test_p1_compose_env_defaults.test_every_expected_key_is_present_in_compose:
# if an extraction regex and a file's actual layout have drifted apart (or a
# line lost its VAULT_IMAGE_TAG fallback / LABEL entirely), THESE fail, not a
# same-looking value mismatch below -- and not a silent None/empty-dict pass
# in the comparison tests that follow.
# ==========================================================================


def test_compose_has_the_expected_image_tag_lines() -> None:
    found = _compose_image_tag_defaults()
    assert set(found) == {"vault-core", "vault-api", "vault-dns"}, (
        f"deploy/compose.yaml: expected exactly the vault-core/vault-api/"
        f"vault-dns image lines to carry a ${{VAULT_IMAGE_TAG:-...}} default, "
        f"found {sorted(found)} -- the extraction regex in this test file and "
        "compose.yaml's actual layout have diverged."
    )


def test_all_compose_image_tag_default_occurrences_agree() -> None:
    """vault-core, vault-api and vault-dns share ONE conceptual release
    number -- a hand-edit that bumped only one of the three `image:` lines
    (typo, partial find-and-replace) must fail here, distinctly from the
    `__version__`-vs-compose comparison below, so a reader immediately knows
    which of the two possible drifts happened.
    """
    defaults = _compose_image_tag_defaults()
    distinct = set(defaults.values())
    assert len(distinct) == 1, (
        f"deploy/compose.yaml's three image: lines disagree on the "
        f"VAULT_IMAGE_TAG default: {defaults} -- they must all carry the "
        "same release number."
    )


def test_dockerfile_has_an_oci_version_label() -> None:
    assert _dockerfile_oci_version() is not None, (
        "api/Dockerfile: no org.opencontainers.image.version=\"...\" LABEL "
        "found -- the extraction regex in this test file and the "
        "Dockerfile's actual layout have diverged, or the LABEL was removed."
    )


def test_verify_stack_has_a_vault_image_tag_default() -> None:
    assert _verify_stack_tag_default() is not None, (
        "deploy/tests/verify-stack.sh: no TAG=${VAULT_IMAGE_TAG:-...} line "
        "found -- the extraction regex in this test file and the script's "
        "actual layout have diverged, or the line was removed/reshaped."
    )


def test_env_example_has_a_vault_image_tag_default() -> None:
    assert _env_example_tag_default() is not None, (
        "deploy/.env.example: no #VAULT_IMAGE_TAG=... example line found -- "
        "the extraction regex in this test file and the file's actual "
        "layout have diverged, or the line was removed/reshaped."
    )


# ==========================================================================
# The pins: vault_api.__version__ vs. each site, one named test per site so
# a drift direction is identifiable from the test name alone.
# ==========================================================================


def test_vault_api_version_matches_compose_image_tag_default() -> None:
    """Mutation-verified in both directions (see this file's module
    docstring) -- bumping either side alone, without the other, fails this
    exact assertion.
    """
    compose_default = _compose_image_tag_defaults().get("vault-api")
    assert compose_default == VAULT_API_VERSION, (
        f"deploy/compose.yaml's VAULT_IMAGE_TAG default is "
        f"{compose_default!r}, but vault_api.__version__ is "
        f"{VAULT_API_VERSION!r} -- one of the two changed without the "
        "other. Bump both together (api/vault_api/__init__.py AND every "
        "image: line in deploy/compose.yaml)."
    )


def test_vault_api_version_matches_dockerfile_oci_label() -> None:
    """Review round 1 blocker B1: this site was missing entirely from the
    first version of this file. A build-time LABEL cannot read
    ``vault_api.__version__`` at build time (no Python import happens while
    Docker evaluates a LABEL instruction), so it stays its own hand-edited
    literal, checked here instead. Mutation-verified in both directions.
    """
    label_value = _dockerfile_oci_version()
    assert label_value == VAULT_API_VERSION, (
        f"api/Dockerfile's org.opencontainers.image.version LABEL is "
        f"{label_value!r}, but vault_api.__version__ is "
        f"{VAULT_API_VERSION!r} -- one of the two changed without the "
        "other. Bump both together."
    )


def test_vault_api_version_matches_verify_stack_tag_default() -> None:
    tag_default = _verify_stack_tag_default()
    assert tag_default == VAULT_API_VERSION, (
        f"deploy/tests/verify-stack.sh's VAULT_IMAGE_TAG default is "
        f"{tag_default!r}, but vault_api.__version__ is "
        f"{VAULT_API_VERSION!r} -- one of the two changed without the "
        "other. Bump both together."
    )


def test_vault_api_version_matches_env_example_tag_default() -> None:
    tag_default = _env_example_tag_default()
    assert tag_default == VAULT_API_VERSION, (
        f"deploy/.env.example's VAULT_IMAGE_TAG example default is "
        f"{tag_default!r}, but vault_api.__version__ is "
        f"{VAULT_API_VERSION!r} -- one of the two changed without the "
        "other. Bump both together."
    )


# ==========================================================================
# Wiring: main.py's `FastAPI(version=VAULT_API_VERSION)` actually uses the
# constant, not a copy of it (review round 1, should-fix S1).
# ==========================================================================


def test_create_app_uses_vault_api_version_as_its_fastapi_version(
    tmp_path: Path,
) -> None:
    """Before this test existed, `version=VAULT_API_VERSION` in `main.py`
    was enforced by a comment only -- hand-changing it to a literal
    `version="9.9.9"` survived the entire 1610-test suite. The blast radius
    of that particular drift is bounded (``openapi_url=None`` disables the
    only route that would ever echo ``app.version`` over HTTP -- confirmed
    empirically: ``GET /openapi.json`` answers ``404``, not the schema), but
    "bounded impact" is not the same as "tested", so this pins the object
    directly rather than relying on an HTTP round trip that cannot see it.
    """
    settings = Settings(
        vault_api_key="test-key-for-version-pin",
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )
    app = create_app(settings)
    assert app.version == VAULT_API_VERSION
