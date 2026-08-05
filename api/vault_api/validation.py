"""Shared request-validation types (WP 2.4 review).

One place, so the coercion semantics of an app id cannot drift apart between
the endpoints that accept one. Before this module, ``POST /v1/prefill`` and
``POST /v1/agent/installed`` each declared their own
``Annotated[int, Field(ge=1)]`` — identical by coincidence, not by
construction.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator, Field


def reject_bool(value: Any) -> Any:
    """Refuse ``true``/``false`` where an id is expected.

    Pydantic's default (lax) mode accepts a JSON boolean for an ``int`` field
    because ``bool`` is an ``int`` subclass in Python, so ``[true]`` would
    silently become app id 1 — a real Steam app id, not an obvious error.

    That mattered even before it was a validation question: the *read* path
    already refuses booleans (``agent_reports._decode_appids`` excludes them
    when decoding a stored snapshot), so without this the write path was the
    more permissive of the two and could store a value its own reader would
    then drop. A JSON client that sends ``true`` for an app id is broken; it
    should be told so (422) rather than have vault-api guess.
    """
    if isinstance(value, bool):
        raise ValueError("must be an integer app id, not a boolean")
    return value


#: A Steam app id in a request body: a positive integer, never a boolean.
#: Numeric strings ("440") are still coerced — that is Pydantic's documented
#: lax-mode behavior and it applies uniformly to every endpoint using this
#: type; the ``ge=1`` bound is enforced after the coercion, so "0" is a 422.
AppId = Annotated[int, BeforeValidator(reject_bool), Field(ge=1)]
