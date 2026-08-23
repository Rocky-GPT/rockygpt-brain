"""Connection pool and idempotent schema migration for the brain's own
Postgres schema."""

from __future__ import annotations

from importlib import resources

import asyncpg


async def create_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=10)
    if pool is None:  # pragma: no cover - asyncpg contract, defensive only
        raise RuntimeError("Failed to create database connection pool.")
    return pool


async def apply_schema(pool: asyncpg.Pool) -> None:
    schema_sql = resources.files(__package__).joinpath("schema.sql").read_text("utf-8")
    async with pool.acquire() as connection:
        await connection.execute(schema_sql)


async def ping(pool: asyncpg.Pool, *, timeout_seconds: float = 2.0) -> bool:
    try:
        async with pool.acquire() as connection:
            await connection.fetchval("SELECT 1", timeout=timeout_seconds)
        return True
    except (OSError, asyncpg.PostgresError, TimeoutError):
        return False
