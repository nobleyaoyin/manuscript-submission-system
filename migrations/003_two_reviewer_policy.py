"""Add prospective review policy markers; no historical records are backfilled."""
import argparse
from pathlib import Path
import sqlite3

MIGRATION_ID = '003_two_reviewer_policy'
DDL = """CREATE TABLE manuscript_review_policies (
    manuscript_id INTEGER PRIMARY KEY REFERENCES manuscripts(manuscript_id),
    minimum_completed_reviewers INTEGER NOT NULL CHECK (minimum_completed_reviewers = 2),
    created_at DATETIME NOT NULL
)"""


def migrate(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('Database must be an existing file')
    connection = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        objects = dict(connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"))
        if not {'schema_migrations', 'manuscripts', 'reviewer_invitations'}.issubset(objects):
            raise RuntimeError('Missing prior migration prerequisites')
        if not connection.execute("SELECT 1 FROM schema_migrations WHERE migration_id='002_reviewer_invitations'").fetchone():
            raise RuntimeError('Migration 002 is required')
        columns = {row[1] for row in connection.execute('PRAGMA table_info(manuscripts)')}
        if not {'manuscript_id', 'status'}.issubset(columns):
            raise RuntimeError('Incompatible manuscripts table')
        applied = connection.execute('SELECT 1 FROM schema_migrations WHERE migration_id=?', (MIGRATION_ID,)).fetchone()
        normalize = lambda sql: ' '.join(sql.lower().split())
        if applied:
            if not objects.get('manuscript_review_policies') or normalize(objects['manuscript_review_policies']) != normalize(DDL):
                raise RuntimeError('Review policy schema mismatch')
        else:
            if 'manuscript_review_policies' in objects:
                raise RuntimeError('Review policy table exists without ledger entry')
            connection.execute(DDL)
            connection.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'))", (MIGRATION_ID,))
        if connection.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Foreign key check failed')
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', help='Explicit path to an existing SQLite database')
    migrate(parser.parse_args().database)
