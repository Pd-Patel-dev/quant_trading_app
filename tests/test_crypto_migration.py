"""Crypto migration tests."""

from data.database import DatabaseManager


def test_migration_v6(temp_db) -> None:
    assert temp_db.schema_version >= 6
    with temp_db.connect() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "crypto_strategy_ledger" in tables
    assert "crypto_strategy_positions" in tables
    if temp_db.schema_version >= 7:
        assert "strategy_lifecycle_events" in tables


def test_migration_idempotent_v6(temp_db) -> None:
    db2 = DatabaseManager(temp_db._database_path)
    assert db2.schema_version == temp_db.schema_version
