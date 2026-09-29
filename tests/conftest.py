"""Shared setup: no test reaches Typesafe, and ledger tests use a disposable local database."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from rockygpt_brain.api.app import app, jev_service
from rockygpt_brain.spending import close_pools

MIGRATIONS = sorted((Path(__file__).parents[1] / "migrations").glob("*.sql"))
DOLLAR = 1_000_000_000


@pytest.fixture(autouse=True)
def no_real_jev() -> Iterator[None]:
    """Jev is off unless a test hands the app its own."""
    app.dependency_overrides[jev_service] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def database() -> str:
    url = os.getenv("BRAIN_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set BRAIN_TEST_DATABASE_URL to a disposable local PostgreSQL database")
    parts = conninfo_to_dict(url)
    if parts.get("host") not in {"127.0.0.1", "localhost"} or (
            parts.get("dbname") != "brain_accounting_test"):
        pytest.fail("Ledger tests only run against localhost/brain_accounting_test")
    with psycopg.connect(url, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('brain_ops.accounts')").fetchone() == (None,):
            for migration in MIGRATIONS:
                conn.execute(migration.read_text().encode())
    return url


@pytest.fixture
def admin(database: str) -> Iterator[psycopg.Connection[Any]]:
    """A clean ledger with a $1 cap in each environment and nothing spent."""
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute("TRUNCATE brain_ops.operations, brain_ops.monthly_allowances")
        conn.execute("UPDATE brain_ops.accounts SET cap_nusd = %s, paused = false", (DOLLAR,))
        yield conn
    close_pools()
