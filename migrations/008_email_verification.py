"""Add email verification state and single-use challenges."""

import argparse
import sqlite3
from pathlib import Path

MIGRATION_ID = "008_email_verification"
PREREQUISITE = "007_email_outbox"

DDL = {
    "user_email_verifications": """
        CREATE TABLE user_email_verifications (
            user_id INTEGER NOT NULL PRIMARY KEY
                REFERENCES users(user_id) ON DELETE CASCADE,
            email TEXT NOT NULL,
            verified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (length(trim(email)) > 0),
            CHECK (verified_at IS NULL OR verified_at >= created_at)
        )
    """,
    "email_verification_challenges": """
        CREATE TABLE email_verification_challenges (
            challenge_id TEXT NOT NULL PRIMARY KEY,
            user_id INTEGER NOT NULL
                REFERENCES users(user_id) ON DELETE CASCADE,
            email TEXT NOT NULL,
            token_digest TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            CHECK (length(token_digest) = 64),
            CHECK (expires_at > created_at),
            CHECK (consumed_at IS NULL OR consumed_at >= created_at)
        )
    """,
    "ix_email_verification_challenges_user": """
        CREATE INDEX ix_email_verification_challenges_user
        ON email_verification_challenges(user_id, expires_at)
    """,
}


def migrate(path):
    path = Path(path).resolve(strict=True)

    if not path.is_file():
        raise ValueError("Database must be an existing file")

    with sqlite3.connect(path.as_uri() + "?mode=rw", uri=True) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")

        try:
            migrations = {
                row[0]
                for row in db.execute(
                    "SELECT migration_id FROM schema_migrations"
                )
            }

            if PREREQUISITE not in migrations:
                raise RuntimeError("Migration 007 must be applied first")

            if MIGRATION_ID not in migrations:
                existing = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master"
                    )
                }

                conflicts = set(DDL) & existing
                if conflicts:
                    raise RuntimeError(
                        f"Database objects already exist: {conflicts}"
                    )

                for sql in DDL.values():
                    db.execute(sql)

                db.execute(
                    """
                    INSERT INTO schema_migrations
                    (migration_id, applied_at)
                    VALUES (?, datetime('now'))
                    """,
                    (MIGRATION_ID,),
                )

            required = set(DDL)
            existing = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master"
                )
            }

            if not required.issubset(existing):
                raise RuntimeError("Email verification schema incomplete")

            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Database integrity check failed")

            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Foreign key check failed")

            db.commit()

        except Exception:
            db.rollback()
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="Existing SQLite database path")
    migrate(parser.parse_args().database)
