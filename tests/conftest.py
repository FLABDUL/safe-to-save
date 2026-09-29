import os
from pathlib import Path

import pytest
from psycopg import sql

from safe_to_save.db import apply_migrations, connect
from safe_to_save.repository import HistoryRepository


@pytest.fixture
def repository() -> HistoryRepository:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("Integration requires the isolated DATABASE_URL")
    # Validation occurs before any connection or destructive test cleanup.
    repository = HistoryRepository(dsn)
    apply_migrations(dsn, Path(__file__).parents[1] / "sql")
    with connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public' AND tablename <> 'schema_migrations'
            ORDER BY tablename
        """)
        for (table_name,) in cursor.fetchall():
            cursor.execute(
                sql.SQL("TRUNCATE TABLE {} CASCADE").format(sql.Identifier(table_name))
            )
    return repository
