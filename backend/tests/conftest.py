"""Temporary Postgres for schema tests. Never uses DATABASE_URL."""

from __future__ import annotations

import getpass
import shutil
import socket
import subprocess
from pathlib import Path

import psycopg
import pytest

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "supabase" / "migrations" / "20261121100000_central_schema.sql"
SEED = REPO / "supabase" / "seed.sql"
CLUSTER = Path(__file__).resolve().parents[1] / ".pgtest"


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(args: list[str]) -> None:
    completed = subprocess.run(args, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{args[0]} failed ({completed.returncode})\n"
            f"{completed.stdout}\n{completed.stderr}"
        )


def _psql(dsn: str, path: Path) -> None:
    completed = subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-f", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"psql {path.name} failed\n{completed.stdout}\n{completed.stderr}")


@pytest.fixture(scope="session")
def db():
    if CLUSTER.exists():
        subprocess.run(
            ["pg_ctl", "-D", str(CLUSTER / "data"), "-m", "immediate", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(CLUSTER)
    data = CLUSTER / "data"
    data.mkdir(parents=True)
    user = getpass.getuser()
    port = _port()
    log = CLUSTER / "server.log"
    started = False
    conn = None
    try:
        _run([
            "initdb", "-D", str(data), "--auth=trust", "--username", user,
            "--encoding=UTF8", "--locale=C",
        ])
        _run([
            "pg_ctl", "-D", str(data), "-l", str(log), "-w", "start",
            "-o", f"-p {port} -c listen_addresses=127.0.0.1 "
                  f"-c unix_socket_directories={data}",
        ])
        started = True
        dsn = f"postgresql://{user}@127.0.0.1:{port}/postgres"
        conn = psycopg.connect(dsn, autocommit=True)
        conn.test_dsn = dsn  # local cluster only; tests must not use DATABASE_URL
        conn.execute(
            """
            do $$
            begin
              if not exists (select 1 from pg_roles where rolname = 'anon') then
                create role anon nologin;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'authenticated') then
                create role authenticated nologin;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'service_role') then
                create role service_role nologin;
              end if;
            end $$;
            """
        )
        _psql(dsn, MIGRATION)
        _psql(dsn, SEED)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        if started:
            subprocess.run(
                ["pg_ctl", "-D", str(data), "-w", "-m", "fast", "stop"],
                check=False,
                capture_output=True,
                text=True,
            )
        shutil.rmtree(CLUSTER, ignore_errors=True)
