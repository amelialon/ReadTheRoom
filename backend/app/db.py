"""Connection pool. The database password stays in this process."""

from __future__ import annotations

from typing import Iterator

from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def _use_utc(conn) -> None:
    conn.execute("set time zone 'UTC'")


def open_pool(database_url: str) -> ConnectionPool:
    pool = ConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row, "autocommit": True},
        configure=_use_utc,
        open=True,
    )
    return pool


def get_conn(request: Request) -> Iterator:
    with request.app.state.pool.connection() as conn:
        yield conn
