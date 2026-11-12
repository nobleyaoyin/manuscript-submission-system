"""Add formatter workflow and local publication records, without backfill."""
import argparse
from pathlib import Path
import sqlite3

ID = '006_formatter_publication'
DDL = {
 'formatter_invitations': """CREATE TABLE formatter_invitations (invitation_id INTEGER PRIMARY KEY, manuscript_id INTEGER NOT NULL REFERENCES manuscripts(manuscript_id), formatter_id INTEGER NOT NULL REFERENCES users(user_id), invited_by INTEGER NOT NULL REFERENCES users(user_id), final_submission_id INTEGER NOT NULL REFERENCES final_editing_submissions(submission_id), status VARCHAR(20) NOT NULL CHECK (status IN ('pending','accepted','declined')), invited_at DATETIME NOT NULL, responded_at DATETIME)""",
 'uq_formatter_active': "CREATE UNIQUE INDEX uq_formatter_active ON formatter_invitations(manuscript_id) WHERE status IN ('pending', 'accepted')",
 'formatting_submissions': """CREATE TABLE formatting_submissions (submission_id INTEGER PRIMARY KEY, manuscript_id INTEGER NOT NULL REFERENCES manuscripts(manuscript_id), invitation_id INTEGER NOT NULL REFERENCES formatter_invitations(invitation_id), file_path VARCHAR(500) NOT NULL, sha256 VARCHAR(64) NOT NULL, status VARCHAR(30) NOT NULL, comments TEXT NOT NULL, created_at DATETIME NOT NULL, author_comments TEXT, author_reviewed_at DATETIME, eic_comments TEXT, eic_reviewed_at DATETIME, eic_id INTEGER REFERENCES users(user_id))""",
 'uq_formatting_current': "CREATE UNIQUE INDEX uq_formatting_current ON formatting_submissions(manuscript_id) WHERE status IN ('pending_author', 'pending_eic', 'approved')",
 'prototype_publications': """CREATE TABLE prototype_publications (manuscript_id INTEGER PRIMARY KEY REFERENCES manuscripts(manuscript_id), submission_id INTEGER NOT NULL UNIQUE REFERENCES formatting_submissions(submission_id), published_by INTEGER NOT NULL REFERENCES users(user_id), published_at DATETIME NOT NULL, doi_status VARCHAR(30) NOT NULL)""",
}

def migrate(path):
    path=Path(path).resolve(strict=True)
    if not path.is_file(): raise ValueError('Database must be an existing file')
    db=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True)
    try:
        db.execute('PRAGMA foreign_keys=ON'); db.execute('BEGIN IMMEDIATE')
        objects=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type IN ('table','index')"))
        if not {'schema_migrations','users','manuscripts','final_editing_submissions','final_editing_submission_invitations'}.issubset(objects): raise RuntimeError('Missing prerequisites')
        if not db.execute("SELECT 1 FROM schema_migrations WHERE migration_id='005_final_editor_workflow'").fetchone(): raise RuntimeError('Migration 005 is required')
        applied=db.execute('SELECT 1 FROM schema_migrations WHERE migration_id=?',(ID,)).fetchone()
        if applied:
            if any(not objects.get(k) or ' '.join(objects[k].lower().split())!=' '.join(v.lower().split()) for k,v in DDL.items()): raise RuntimeError('Formatter schema mismatch')
        else:
            if any(k in objects for k in DDL): raise RuntimeError('Formatter objects exist without ledger entry')
            for sql in DDL.values(): db.execute(sql)
            db.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'))",(ID,))
        if db.execute('PRAGMA foreign_key_check').fetchone(): raise RuntimeError('Foreign key check failed')
        db.commit()
    except Exception:
        db.rollback(); raise
    finally: db.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('database');migrate(p.parse_args().database)
