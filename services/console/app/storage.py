from __future__ import annotations

import asyncpg


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS console_sites (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    domain TEXT NOT NULL UNIQUE,
    verification_token TEXT NOT NULL,
    verification_method TEXT,
    verified_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS console_inspections (
    id BIGSERIAL PRIMARY KEY,
    site_id BIGINT NOT NULL REFERENCES console_sites(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    indexed_status BOOLEAN NOT NULL,
    last_crawled DOUBLE PRECISION,
    issues TEXT NOT NULL DEFAULT '',
    inspected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_id, url)
);

CREATE TABLE IF NOT EXISTS console_analytics_events (
    id BIGSERIAL PRIMARY KEY,
    site_id BIGINT NOT NULL REFERENCES console_sites(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('click', 'impression')),
    happened_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


async def init_db(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
