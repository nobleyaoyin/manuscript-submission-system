"""Add empty email outbox tables. Explicit existing database path required."""
import argparse
from pathlib import Path
import sqlite3

MIGRATION_ID = '007_email_outbox'
PREREQUISITES = (
    '001_reviewer_file_bindings', '002_reviewer_invitations',
    '003_two_reviewer_policy', '004_author_agreements',
    '005_final_editor_workflow', '006_formatter_publication',
)
UTC_GLOB = ('[0-9]' * 4 + '-' + '[0-9]' * 2 + '-' + '[0-9]' * 2 +
            'T' + '[0-9]' * 2 + ':' + '[0-9]' * 2 + ':' + '[0-9]' * 2 +
            '.' + '[0-9]' * 6 + 'Z')


def utc_check(column, nullable=False):
    pattern = f"{column} GLOB '{UTC_GLOB}'"
    return f'{column} IS NULL OR ({pattern})' if nullable else pattern


DDL = {
    'notification_events': f"""CREATE TABLE notification_events (
        event_id TEXT NOT NULL PRIMARY KEY,
        event_type TEXT NOT NULL,
        invitation_assignment_id INTEGER NOT NULL REFERENCES reviewer_invitations(assignment_id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        CONSTRAINT ck_notification_event_type CHECK (event_type = 'reviewer.invitation.created'),
        CONSTRAINT ck_notification_event_version CHECK (schema_version = 1),
        CONSTRAINT ck_notification_event_created_utc CHECK ({utc_check('created_at')}),
        CONSTRAINT uq_notification_event_source UNIQUE (event_type, invitation_assignment_id)
    )""",
    'email_outbox': f"""CREATE TABLE email_outbox (
        outbox_id TEXT NOT NULL PRIMARY KEY,
        event_id TEXT NOT NULL REFERENCES notification_events(event_id) ON DELETE RESTRICT,
        recipient_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
        template_key TEXT NOT NULL,
        template_version TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        lease_token TEXT,
        lease_until TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        accepted_at TEXT,
        provider_message_id TEXT,
        terminal_reason TEXT,
        CONSTRAINT uq_email_outbox_event_recipient UNIQUE (event_id, recipient_user_id),
        CONSTRAINT ck_email_outbox_status CHECK (status IN ('pending','processing','retry_wait','blocked_verification','accepted','failed','unknown','cancelled')),
        CONSTRAINT ck_email_outbox_attempt_count CHECK (typeof(attempt_count) = 'integer' AND attempt_count >= 0),
        CONSTRAINT ck_email_outbox_lease CHECK ((status = 'processing' AND lease_token IS NOT NULL AND length(trim(lease_token)) > 0 AND lease_until IS NOT NULL) OR (status <> 'processing' AND lease_token IS NULL AND lease_until IS NULL)),
        CONSTRAINT ck_email_outbox_due CHECK ((status IN ('pending','retry_wait') AND next_attempt_at IS NOT NULL) OR (status NOT IN ('pending','retry_wait') AND next_attempt_at IS NULL)),
        CONSTRAINT ck_email_outbox_accepted CHECK ((status = 'accepted' AND accepted_at IS NOT NULL) OR (status <> 'accepted' AND accepted_at IS NULL)),
        CONSTRAINT ck_email_outbox_created_utc CHECK ({utc_check('created_at')}),
        CONSTRAINT ck_email_outbox_updated_utc CHECK ({utc_check('updated_at')}),
        CONSTRAINT ck_email_outbox_due_utc CHECK ({utc_check('next_attempt_at', True)}),
        CONSTRAINT ck_email_outbox_lease_utc CHECK ({utc_check('lease_until', True)}),
        CONSTRAINT ck_email_outbox_accepted_utc CHECK ({utc_check('accepted_at', True)}),
        CONSTRAINT ck_email_outbox_provider_id CHECK (provider_message_id IS NULL OR length(provider_message_id) BETWEEN 1 AND 255),
        CONSTRAINT ck_email_outbox_reason CHECK (terminal_reason IS NULL OR length(terminal_reason) BETWEEN 1 AND 64)
    )""",
    'email_delivery_attempts': f"""CREATE TABLE email_delivery_attempts (
        attempt_id TEXT NOT NULL PRIMARY KEY,
        outbox_id TEXT NOT NULL REFERENCES email_outbox(outbox_id) ON DELETE RESTRICT,
        attempt_number INTEGER NOT NULL,
        lease_token TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        outcome TEXT NOT NULL,
        error_code TEXT,
        provider_message_id TEXT,
        CONSTRAINT uq_email_attempt_number UNIQUE (outbox_id, attempt_number),
        CONSTRAINT ck_email_attempt_number CHECK (typeof(attempt_number) = 'integer' AND attempt_number > 0),
        CONSTRAINT ck_email_attempt_outcome CHECK (outcome IN ('started','accepted','retryable','permanent','unknown')),
        CONSTRAINT ck_email_attempt_finished CHECK ((outcome = 'started' AND finished_at IS NULL) OR (outcome <> 'started' AND finished_at IS NOT NULL)),
        CONSTRAINT ck_email_attempt_started_utc CHECK ({utc_check('started_at')}),
        CONSTRAINT ck_email_attempt_finished_utc CHECK ({utc_check('finished_at', True)}),
        CONSTRAINT ck_email_attempt_lease CHECK (length(trim(lease_token)) > 0),
        CONSTRAINT ck_email_attempt_error_code CHECK (error_code IS NULL OR length(error_code) BETWEEN 1 AND 64),
        CONSTRAINT ck_email_attempt_provider_id CHECK (provider_message_id IS NULL OR length(provider_message_id) BETWEEN 1 AND 255)
    )""",
    'ix_email_outbox_due': 'CREATE INDEX ix_email_outbox_due ON email_outbox (status, next_attempt_at)',
    'ix_email_outbox_lease': 'CREATE INDEX ix_email_outbox_lease ON email_outbox (status, lease_until)',
    'ix_email_attempt_outbox_started': 'CREATE INDEX ix_email_attempt_outbox_started ON email_delivery_attempts (outbox_id, started_at)',
}


def migrate(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('Database must be an existing file')
    db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        objects = {name: (kind, sql) for name, kind, sql in db.execute(
            "SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','index','view','trigger')"
        )}
        required = {'schema_migrations', 'users', 'manuscripts', 'review_assignments',
                    'reviewer_invitations', 'review_round_files', 'review_assignment_files',
                    'manuscript_review_policies', 'author_agreements', 'final_editor_invitations',
                    'final_editing_submission_invitations', 'formatter_invitations',
                    'formatting_submissions', 'prototype_publications'}
        if any(name not in objects or objects[name][0] != 'table' for name in required):
            raise RuntimeError('Missing prerequisite tables')
        ledger = {row[0] for row in db.execute('SELECT migration_id FROM schema_migrations')}
        if not set(PREREQUISITES).issubset(ledger):
            raise RuntimeError('Migrations 001 through 006 are required')
        for table, columns in {'users': {'user_id'}, 'reviewer_invitations': {'assignment_id'}}.items():
            if not columns.issubset({row[1] for row in db.execute(f'PRAGMA table_info({table})')}):
                raise RuntimeError('Incompatible prerequisite schema')
        if MIGRATION_ID not in ledger:
            if any(name in objects for name in DDL):
                raise RuntimeError('Email outbox objects exist without ledger entry')
            for sql in DDL.values():
                db.execute(sql)
            db.execute('INSERT INTO schema_migrations VALUES (?, datetime(\'now\'))', (MIGRATION_ID,))
        objects = {name: (kind, sql) for name, kind, sql in db.execute(
            "SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','index','view','trigger')"
        )}
        normalize = lambda sql: ' '.join(sql.lower().split())
        for name, expected in DDL.items():
            expected_type = 'index' if name.startswith('ix_') else 'table'
            if name not in objects or objects[name][0] != expected_type or not objects[name][1] or normalize(objects[name][1]) != normalize(expected):
                raise RuntimeError('Email outbox schema mismatch')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Foreign key check failed')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', help='Explicit path to an existing SQLite database')
    migrate(parser.parse_args().database)
