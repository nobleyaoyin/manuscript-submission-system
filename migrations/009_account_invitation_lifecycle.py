"""Add empty lifecycle tables. Explicit existing SQLite path required."""
import argparse
from importlib import import_module
from pathlib import Path
import sqlite3
import re

MIGRATION_ID = '009_account_invitation_lifecycle'
PREREQUISITES = (
    '001_reviewer_file_bindings', '002_reviewer_invitations',
    '003_two_reviewer_policy', '004_author_agreements',
    '005_final_editor_workflow', '006_formatter_publication',
    '007_email_outbox', '008_email_verification',
)

DDL = {'account_lifecycle_events': 'CREATE TABLE account_lifecycle_events (\n'
                             '    event_id TEXT NOT NULL PRIMARY KEY CHECK (length(event_id) BETWEEN 1 AND '
                             '64),\n'
                             '    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,\n'
                             "    sequence INTEGER NOT NULL CHECK (typeof(sequence) = 'integer' AND sequence "
                             '>= 1),\n'
                             '    from_state TEXT CHECK (from_state IS NULL OR from_state IN '
                             "('active','disabled')),\n"
                             "    to_state TEXT NOT NULL CHECK (to_state IN ('active','disabled')),\n"
                             '    auth_generation INTEGER NOT NULL CHECK (typeof(auth_generation) = '
                             "'integer' AND auth_generation >= 1),\n"
                             '    actor_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE '
                             'RESTRICT,\n'
                             '    occurred_at TEXT NOT NULL CHECK (\n'
                             '      occurred_at GLOB '
                             "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'),\n"
                             '    reason_code TEXT NOT NULL CHECK (reason_code IN '
                             "('initial_enrollment','administrative_disable','administrative_reactivation')),\n"
                             "    CHECK ((sequence = 1 AND from_state IS NULL AND to_state = 'active' AND "
                             "reason_code = 'initial_enrollment')\n"
                             "        OR (sequence > 1 AND from_state IS NOT NULL AND from_state = 'active' "
                             "AND to_state = 'disabled' AND reason_code = 'administrative_disable')\n"
                             '        OR (sequence > 1 AND from_state IS NOT NULL AND from_state = '
                             "'disabled' AND to_state = 'active' AND reason_code = "
                             "'administrative_reactivation')),\n"
                             '    UNIQUE(user_id, sequence),\n'
                             '    UNIQUE(event_id, user_id, sequence, to_state, auth_generation)\n'
                             ')',
 'account_lifecycle': 'CREATE TABLE account_lifecycle (\n'
                      '    user_id INTEGER NOT NULL PRIMARY KEY REFERENCES users(user_id) ON DELETE '
                      'RESTRICT,\n'
                      "    state TEXT NOT NULL CHECK (state IN ('active','disabled')),\n"
                      "    sequence INTEGER NOT NULL CHECK (typeof(sequence) = 'integer' AND sequence >= "
                      '1),\n'
                      "    auth_generation INTEGER NOT NULL CHECK (typeof(auth_generation) = 'integer' AND "
                      'auth_generation >= 1),\n'
                      '    last_event_id TEXT NOT NULL,\n'
                      '    FOREIGN KEY(last_event_id, user_id, sequence, state, auth_generation)\n'
                      '      REFERENCES account_lifecycle_events(event_id, user_id, sequence, to_state, '
                      'auth_generation)\n'
                      '      ON DELETE RESTRICT\n'
                      ')',
 'ix_lifecycle_events_actor_time': 'CREATE INDEX ix_lifecycle_events_actor_time ON '
                                   'account_lifecycle_events(actor_user_id, occurred_at)',
 'reviewer_invitation_invalidations': 'CREATE TABLE reviewer_invitation_invalidations (\n'
                                      '    invalidation_id TEXT NOT NULL PRIMARY KEY CHECK '
                                      '(length(invalidation_id) BETWEEN 1 AND 64),\n'
                                      '    assignment_id INTEGER NOT NULL UNIQUE REFERENCES '
                                      'reviewer_invitations(assignment_id) ON DELETE RESTRICT,\n'
                                      "    kind TEXT NOT NULL CHECK (kind IN ('cancelled','superseded')),\n"
                                      '    replacement_assignment_id INTEGER REFERENCES '
                                      'reviewer_invitations(assignment_id) ON DELETE RESTRICT,\n'
                                      '    actor_user_id INTEGER NOT NULL REFERENCES users(user_id) ON '
                                      'DELETE RESTRICT,\n'
                                      '    occurred_at TEXT NOT NULL CHECK (\n'
                                      '      occurred_at GLOB '
                                      "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'),\n"
                                      '    reason_code TEXT NOT NULL CHECK (reason_code IN '
                                      "('editorial_cancellation','reviewer_unavailable','replacement_selected')),\n"
                                      "    CHECK ((kind = 'cancelled' AND replacement_assignment_id IS NULL\n"
                                      '              AND reason_code IN '
                                      "('editorial_cancellation','reviewer_unavailable'))\n"
                                      "        OR (kind = 'superseded' AND replacement_assignment_id IS NOT "
                                      'NULL\n'
                                      '              AND replacement_assignment_id <> assignment_id AND '
                                      "reason_code = 'replacement_selected'))\n"
                                      ')',
 'ix_reviewer_invalidation_actor_time': 'CREATE INDEX ix_reviewer_invalidation_actor_time ON '
                                        'reviewer_invitation_invalidations(actor_user_id, occurred_at)',
 'ix_reviewer_invalidation_replacement': 'CREATE INDEX ix_reviewer_invalidation_replacement ON '
                                         'reviewer_invitation_invalidations(replacement_assignment_id)',
 'lifecycle_events_no_update': 'CREATE TRIGGER lifecycle_events_no_update BEFORE UPDATE ON '
                               'account_lifecycle_events\n'
                               "BEGIN SELECT RAISE(ABORT, 'lifecycle events are append-only'); END",
 'lifecycle_events_no_delete': 'CREATE TRIGGER lifecycle_events_no_delete BEFORE DELETE ON '
                               'account_lifecycle_events\n'
                               "BEGIN SELECT RAISE(ABORT, 'lifecycle events are append-only'); END",
 'reviewer_invalidations_no_update': 'CREATE TRIGGER reviewer_invalidations_no_update BEFORE UPDATE ON '
                                     'reviewer_invitation_invalidations\n'
                                     "BEGIN SELECT RAISE(ABORT, 'reviewer invalidations are append-only'); "
                                     'END',
 'reviewer_invalidations_no_delete': 'CREATE TRIGGER reviewer_invalidations_no_delete BEFORE DELETE ON '
                                     'reviewer_invitation_invalidations\n'
                                     "BEGIN SELECT RAISE(ABORT, 'reviewer invalidations are append-only'); "
                                     'END',
 'uq_invalidation_replacement': 'CREATE UNIQUE INDEX uq_invalidation_replacement ON '
                                'reviewer_invitation_invalidations(replacement_assignment_id) WHERE '
                                'replacement_assignment_id IS NOT NULL'}


def _normalize(sql):
    # Preserve literal contents: changing a CHECK value, case or embedded space
    # changes semantics and must never be accepted as the same schema.
    parts = re.split(r"('(?:''|[^'])*')", sql.strip().rstrip(';'))
    return ''.join(part if i % 2 else re.sub(r'\s+', ' ', part.lower())
                   for i, part in enumerate(parts)).strip()


def _objects(db):
    return {name: (kind, sql, table) for name, kind, sql, table in db.execute(
        "SELECT name,type,sql,tbl_name FROM sqlite_master WHERE type IN ('table','index','trigger','view')")}


def _validate_prerequisites(db, objects):
    if 'schema_migrations' not in objects or objects['schema_migrations'][0] != 'table':
        raise RuntimeError('Missing migration ledger')
    ledger_columns = {row[1] for row in db.execute('PRAGMA table_info(schema_migrations)')}
    if ledger_columns != {'migration_id', 'applied_at'}:
        raise RuntimeError('Incompatible migration ledger')
    ledger = {row[0] for row in db.execute('SELECT migration_id FROM schema_migrations')}
    if not set(PREREQUISITES).issubset(ledger):
        raise RuntimeError('Migrations 001 through 008 are required')
    for name in PREREQUISITES:
        module = import_module('migrations.' + name)
        declared = module.DDL
        if isinstance(declared, str):
            table = {'002_reviewer_invitations': 'reviewer_invitations',
                     '003_two_reviewer_policy': 'manuscript_review_policies',
                     '004_author_agreements': 'author_agreements'}[name]
            declared = {table: declared}
        for obj, expected in declared.items():
            actual = objects.get(obj)
            expected_type = 'index' if expected.strip().lower().startswith(('create index', 'create unique index')) else 'table'
            if not actual or actual[0] != expected_type or not actual[1] or _normalize(actual[1]) != _normalize(expected):
                raise RuntimeError('Incompatible prerequisite schema')
    for table, required in {'users': {'user_id', 'role', 'email'},
                            'review_assignments': {'assignment_id', 'manuscript_id', 'reviewer_id', 'review_round', 'status'},
                            'manuscripts': {'manuscript_id', 'status', 'review_round'}}.items():
        if table not in objects or objects[table][0] != 'table':
            raise RuntimeError('Missing prerequisite table')
        if not required.issubset({r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}):
            raise RuntimeError('Incompatible prerequisite columns')
    return ledger


def _validate_own_schema(objects):
    for name, expected in DDL.items():
        actual = objects.get(name)
        lower = expected.lower()
        kind = 'trigger' if lower.startswith('create trigger') else 'index' if lower.startswith(('create index', 'create unique index')) else 'table'
        if not actual or actual[0] != kind or not actual[1] or _normalize(actual[1]) != _normalize(expected):
            raise RuntimeError('Lifecycle schema mismatch')
    tables = {'account_lifecycle_events', 'account_lifecycle', 'reviewer_invitation_invalidations'}
    extras = {name for name, (kind, sql, table) in objects.items()
              if table in tables and kind in ('trigger', 'index') and sql is not None and name not in DDL}
    if extras:
        raise RuntimeError('Unexpected lifecycle schema objects')


def migrate(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('Database path must be an existing file')
    db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        if db.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise RuntimeError('Foreign key enforcement required')
        db.execute('BEGIN IMMEDIATE')
        objects = _objects(db)
        ledger = _validate_prerequisites(db, objects)
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('SQLite integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Foreign key check failed')
        if MIGRATION_ID not in ledger:
            if any(name in objects for name in DDL):
                raise RuntimeError('Lifecycle objects exist without ledger entry')
            for statement in DDL.values():
                db.execute(statement)
            db.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'))", (MIGRATION_ID,))
        _validate_own_schema(_objects(db))
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Foreign key check failed')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('SQLite integrity check failed')
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
