"""Shared FastAPI dependencies (request-scoped resources).

Kept separate from db.py so db.py stays framework-agnostic (plain sqlite3
helpers, importable without FastAPI installed) while routers get a normal
FastAPI ``Depends(get_db)`` dependency.
"""

from __future__ import annotations

from typing import Iterator

import sqlite3

from fastapi import Request

from vault_api.db import get_connection


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """Yield a per-request SQLite connection, closed when the request ends.

    Uses the same ``get_connection`` helper as startup's ``init_db`` (WAL
    journal mode + busy_timeout), so route handlers don't immediately fail
    with "database is locked" while a background job-queue writer (WP 1.4+)
    holds a write transaction.
    """
    conn = get_connection(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()
