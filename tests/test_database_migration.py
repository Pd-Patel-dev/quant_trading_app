"""Database migration tests."""

from data.database import DatabaseManager


def test_migration_idempotent(temp_db) -> None:
    version1 = temp_db.schema_version
    db2 = DatabaseManager(temp_db._database_path)
    assert db2.schema_version == version1
    recent = temp_db.get_recent_backtests(limit=1)
    assert recent is not None or recent == []


def test_foreign_keys_enabled(temp_db) -> None:
    with temp_db.connect() as conn:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1
