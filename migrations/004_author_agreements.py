"""Create prospective agreement records; never backfill historical manuscripts."""
import argparse
from pathlib import Path
import sqlite3

MIGRATION_ID = '004_author_agreements'
DDL = """CREATE TABLE author_agreements (
    agreement_id INTEGER PRIMARY KEY,
    manuscript_id INTEGER NOT NULL REFERENCES manuscripts(manuscript_id),
    decision_id INTEGER NOT NULL UNIQUE REFERENCES eic_decisions(decision_id),
    author_id INTEGER NOT NULL REFERENCES users(user_id),
    agreement_version VARCHAR(40) NOT NULL,
    agreement_text TEXT NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending', 'accepted', 'declined')),
    created_at DATETIME NOT NULL,
    responded_at DATETIME,
    CHECK ((status = 'pending' AND responded_at IS NULL) OR (status IN ('accepted', 'declined') AND responded_at IS NOT NULL))
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
        if not {'schema_migrations', 'manuscripts', 'users', 'eic_decisions', 'manuscript_review_policies'}.issubset(objects):
            raise RuntimeError('Missing prior migration prerequisites')
        if not connection.execute("SELECT 1 FROM schema_migrations WHERE migration_id='003_two_reviewer_policy'").fetchone():
            raise RuntimeError('Migration 003 is required')
        for table, required in {'manuscripts': {'manuscript_id', 'author_id', 'status'}, 'eic_decisions': {'decision_id', 'manuscript_id', 'decision'}}.items():
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if not required.issubset(columns):
                raise RuntimeError(f'Incompatible table: {table}')
        applied = connection.execute('SELECT 1 FROM schema_migrations WHERE migration_id=?', (MIGRATION_ID,)).fetchone()
        normalize = lambda sql: ' '.join(sql.lower().split())
        if applied:
            if not objects.get('author_agreements') or normalize(objects['author_agreements']) != normalize(DDL):
                raise RuntimeError('Author agreement schema mismatch')
        else:
            if 'author_agreements' in objects:
                raise RuntimeError('Author agreement table exists without ledger entry')
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
