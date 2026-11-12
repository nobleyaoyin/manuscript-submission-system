"""Add prospective final-editor invitations and submission links without backfill."""
import argparse
from pathlib import Path
import sqlite3

MIGRATION_ID = '005_final_editor_workflow'
DDL = {
 'final_editor_invitations': """CREATE TABLE final_editor_invitations (
    invitation_id INTEGER PRIMARY KEY,
    manuscript_id INTEGER NOT NULL REFERENCES manuscripts(manuscript_id),
    editor_id INTEGER NOT NULL REFERENCES users(user_id),
    invited_by INTEGER NOT NULL REFERENCES users(user_id),
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending', 'accepted', 'declined')),
    invited_at DATETIME NOT NULL,
    responded_at DATETIME
 )""",
 'uq_final_editor_active': "CREATE UNIQUE INDEX uq_final_editor_active ON final_editor_invitations(manuscript_id) WHERE status IN ('pending', 'accepted')",
 'final_editing_submission_invitations': """CREATE TABLE final_editing_submission_invitations (
    submission_id INTEGER PRIMARY KEY REFERENCES final_editing_submissions(submission_id),
    invitation_id INTEGER NOT NULL REFERENCES final_editor_invitations(invitation_id)
 )""",
}

def migrate(path):
    path=Path(path).resolve(strict=True)
    if not path.is_file():raise ValueError('Database must be an existing file')
    db=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True)
    try:
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        objects=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type IN ('table','index')"))
        if not {'schema_migrations','manuscripts','users','author_agreements','final_editing_submissions'}.issubset(objects):raise RuntimeError('Missing migration prerequisites')
        if not db.execute("SELECT 1 FROM schema_migrations WHERE migration_id='004_author_agreements'").fetchone():raise RuntimeError('Migration 004 is required')
        for table,columns in {'manuscripts':{'manuscript_id','status'},'final_editing_submissions':{'submission_id','manuscript_id','editor_id','status'}}.items():
            if not columns.issubset({r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}):raise RuntimeError(f'Incompatible table: {table}')
        applied=db.execute('SELECT 1 FROM schema_migrations WHERE migration_id=?',(MIGRATION_ID,)).fetchone()
        normalize=lambda sql:' '.join(sql.lower().split())
        if applied:
            if any(not objects.get(k) or normalize(objects[k])!=normalize(v) for k,v in DDL.items()):raise RuntimeError('Final editor schema mismatch')
        else:
            if any(k in objects for k in DDL):raise RuntimeError('Final editor objects exist without ledger entry')
            for sql in DDL.values():db.execute(sql)
            db.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'))",(MIGRATION_ID,))
        if db.execute('PRAGMA foreign_key_check').fetchone():raise RuntimeError('Foreign key check failed')
        db.commit()
    except Exception:
        db.rollback();raise
    finally:db.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('database');migrate(parser.parse_args().database)
