"""Explicit migration. Pass an existing SQLite file; never runs on app startup."""
import argparse
from pathlib import Path
import sqlite3

MIGRATION_ID = "001_reviewer_file_bindings"
DDL = {
    "review_round_files": """CREATE TABLE review_round_files (
        round_file_id INTEGER PRIMARY KEY,
        manuscript_id INTEGER NOT NULL REFERENCES manuscripts(manuscript_id),
        review_round INTEGER NOT NULL CHECK (review_round >= 1),
        source_kind TEXT NOT NULL CHECK (source_kind IN ('initial_editing', 'author_revision')),
        confirmation_id INTEGER REFERENCES pre_review_confirmations(confirmation_id),
        file_path TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        UNIQUE (manuscript_id, review_round),
        CHECK ((review_round = 1 AND source_kind = 'initial_editing' AND confirmation_id IS NOT NULL)
            OR (review_round > 1 AND source_kind = 'author_revision' AND confirmation_id IS NULL))
    )""",
    "review_assignment_files": """CREATE TABLE review_assignment_files (
        assignment_id INTEGER PRIMARY KEY REFERENCES review_assignments(assignment_id),
        round_file_id INTEGER NOT NULL REFERENCES review_round_files(round_file_id),
        bound_at DATETIME NOT NULL
    )""",
    "ix_review_assignment_files_round_file_id": "CREATE INDEX ix_review_assignment_files_round_file_id ON review_assignment_files(round_file_id)",
}
REQUIRED = {
    "manuscripts": {"manuscript_id", "review_round", "file_path"},
    "review_assignments": {"assignment_id", "manuscript_id", "review_round"},
    "pre_review_confirmations": {"confirmation_id", "manuscript_id", "submission_id", "file_path"},
}


def normalize(sql):
    return " ".join(sql.lower().split())


def migrate(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("Database path must be an existing file")
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        objects = dict(connection.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'index')"))
        for table, expected in REQUIRED.items():
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if not expected.issubset(columns):
                raise RuntimeError(f"Missing or incompatible existing table: {table}")
        if "schema_migrations" not in objects:
            connection.execute("CREATE TABLE schema_migrations (migration_id TEXT PRIMARY KEY, applied_at DATETIME NOT NULL)")
        else:
            columns = {row[1] for row in connection.execute('PRAGMA table_info("schema_migrations")')}
            if columns != {"migration_id", "applied_at"}:
                raise RuntimeError("Incompatible migration ledger")
        applied = connection.execute("SELECT 1 FROM schema_migrations WHERE migration_id = ?", (MIGRATION_ID,)).fetchone()
        if applied:
            for name, sql in DDL.items():
                if not objects.get(name) or normalize(objects[name]) != normalize(sql):
                    raise RuntimeError(f"Migration schema mismatch: {name}")
        else:
            if any(name in objects for name in DDL):
                raise RuntimeError("Migration objects already exist without ledger entry")
            for sql in DDL.values():
                connection.execute(sql)
            connection.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'))", (MIGRATION_ID,))
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Foreign key check failed")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="Explicit path to an existing SQLite database")
    migrate(parser.parse_args().database)
