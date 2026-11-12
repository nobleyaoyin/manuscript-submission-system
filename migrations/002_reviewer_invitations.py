"""Add reviewer invitations without modifying existing assignments. Run explicitly."""
import argparse
from pathlib import Path
import sqlite3

MIGRATION_ID = '002_reviewer_invitations'
DDL = """CREATE TABLE reviewer_invitations (
    assignment_id INTEGER PRIMARY KEY REFERENCES review_assignments(assignment_id),
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending', 'accepted', 'declined')),
    invited_at DATETIME NOT NULL,
    responded_at DATETIME
)"""

def migrate(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file(): raise ValueError('Database must be an existing file')
    conn = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        objects = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"))
        if not {'schema_migrations', 'review_assignments', 'review_round_files', 'review_assignment_files'}.issubset(objects):
            raise RuntimeError('Missing migration 001 prerequisites')
        if not conn.execute("SELECT 1 FROM schema_migrations WHERE migration_id='001_reviewer_file_bindings'").fetchone():
            raise RuntimeError('Migration 001 is required')
        if not {'assignment_id', 'manuscript_id', 'reviewer_id', 'status'}.issubset({r[1] for r in conn.execute('PRAGMA table_info(review_assignments)')}):
            raise RuntimeError('Incompatible review assignments')
        applied = conn.execute('SELECT 1 FROM schema_migrations WHERE migration_id=?', (MIGRATION_ID,)).fetchone()
        normalize = lambda sql: ' '.join(sql.lower().split())
        if applied:
            if not objects.get('reviewer_invitations') or normalize(objects['reviewer_invitations']) != normalize(DDL):
                raise RuntimeError('Invitation schema mismatch')
        else:
            if 'reviewer_invitations' in objects: raise RuntimeError('Invitation table exists without ledger entry')
            conn.execute(DDL)
            conn.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'))", (MIGRATION_ID,))
        if conn.execute('PRAGMA foreign_key_check').fetchone(): raise RuntimeError('Foreign key check failed')
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', help='Explicit path to an existing SQLite database')
    migrate(parser.parse_args().database)
