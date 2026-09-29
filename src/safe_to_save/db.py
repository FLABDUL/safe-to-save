"""Connections and append-only migrations for the isolated offline database."""

import hashlib
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def isolated_dsn(dsn: str) -> str:
    """Fail closed before connecting, including libpq host-address overrides."""
    try:
        values = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        raise ValueError("Expected the isolated Safe to Save database") from None
    if (
        values.get("host") != "127.0.0.1"
        or values.get("port") != "55433"
        or values.get("dbname") != "safe_to_save"
        or values.get("hostaddr", "127.0.0.1") != "127.0.0.1"
        or "service" in values
    ):
        raise ValueError("Expected the isolated database at 127.0.0.1:55433/safe_to_save")
    return make_conninfo(dsn, hostaddr="127.0.0.1", connect_timeout="5")


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(isolated_dsn(dsn))


def apply_migrations(dsn: str, sql_dir: Path) -> tuple[str, ...]:
    paths = sorted(sql_dir.glob("[0-9][0-9][0-9]_*.sql"))
    if not paths:
        raise ValueError("No SQL migrations found")
    applied_now: list[str] = []
    with connect(dsn) as connection:
        # Serialise the bootstrap and migration check across concurrent CLI processes.
        connection.execute("SELECT pg_advisory_xact_lock(55433, 1)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                sha256 text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )
        """)
        existing = dict(connection.execute("SELECT version, sha256 FROM schema_migrations"))
        sources = {path.name: path.read_bytes() for path in paths}
        if existing.keys() - sources.keys():
            raise ValueError("Applied migration missing from SQL directory")
        for name, source in sources.items():
            checksum = hashlib.sha256(source).hexdigest()
            if name in existing:
                if existing[name] != checksum:
                    raise ValueError(f"Applied migration checksum changed: {name}")
                continue
            if existing and name < max(existing):
                raise ValueError(f"Migration is not append-only: {name}")
            connection.execute(source.decode("utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations(version, sha256) VALUES (%s, %s)",
                (name, checksum),
            )
            applied_now.append(name)
    return tuple(applied_now)
