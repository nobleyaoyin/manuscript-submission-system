"""Internal, default-disabled invitation enqueueing and read-only schema check."""
from enum import Enum
from importlib import import_module

from sqlalchemy.orm import Session


class EnqueueMode(str, Enum):
    DISABLED = 'disabled'
    ENQUEUE_ONLY = 'enqueue_only'


def reviewer_email_enqueue_mode() -> EnqueueMode:
    # No environment, request or account input can enable this dependency.
    return EnqueueMode.DISABLED


class OutboxSchemaUnavailable(RuntimeError):
    pass


def check_outbox_schema(db: Session) -> None:
    """Verify migration 007 definitions using the caller's SQLite connection.

    Importing the migration module only loads its constants; migrate() is never
    invoked. All SQL here is read-only and the check is repeated per request.
    """
    connection = db.connection()
    if connection.dialect.name != 'sqlite' or connection.exec_driver_sql('PRAGMA foreign_keys').scalar() != 1:
        raise OutboxSchemaUnavailable('Outbox schema unavailable')
    spec = import_module('migrations.007_email_outbox')
    objects = {name: (kind, sql) for name, kind, sql in connection.exec_driver_sql(
        "SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','index','view','trigger')"
    )}
    if objects.get('schema_migrations', (None,))[0] != 'table':
        raise OutboxSchemaUnavailable('Outbox schema unavailable')
    ledger = {row[0] for row in connection.exec_driver_sql('SELECT migration_id FROM schema_migrations')}
    if not set(spec.PREREQUISITES + (spec.MIGRATION_ID,)).issubset(ledger):
        raise OutboxSchemaUnavailable('Outbox schema unavailable')
    normalize = lambda sql: ' '.join(sql.lower().split())
    for name, expected in spec.DDL.items():
        kind, sql = objects.get(name, (None, None))
        if kind != ('index' if name.startswith('ix_') else 'table') or not sql or normalize(sql) != normalize(expected):
            raise OutboxSchemaUnavailable('Outbox schema unavailable')
