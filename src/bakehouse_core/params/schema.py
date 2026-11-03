"""Table definitions and seed for the parameter store (spec section 4.2).

Defined once with SQLAlchemy Core so the same tables work on SQLite (tests), Postgres
(local container) and Lakebase. The Delta backend gets its own DDL in a later workstream.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import sqlalchemy as sa

from bakehouse_core.params import model

# Version 1 applies from before any virtual date, so as-of reads always find a value.
SEED_EFFECTIVE_FROM = date(1900, 1, 1)

metadata = sa.MetaData()

param_definitions = sa.Table(
    "param_definitions",
    metadata,
    sa.Column("param_key", sa.String(100), primary_key=True),
    sa.Column("data_type", sa.String(20), nullable=False),
    sa.Column("min_value", sa.String(40), nullable=False),
    sa.Column("max_value", sa.String(40), nullable=False),
    sa.Column("unit", sa.String(40), nullable=False),
    sa.Column("description", sa.String(500), nullable=False),
)

# Append-only: an edit inserts a new version and never updates a row.
param_versions = sa.Table(
    "param_versions",
    metadata,
    sa.Column("param_key", sa.String(100), primary_key=True),
    sa.Column("version", sa.Integer, primary_key=True),
    sa.Column("value_text", sa.String(40), nullable=False),
    sa.Column("effective_from", sa.Date, nullable=False),
    sa.Column("updated_by", sa.String(200), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("change_note", sa.String(500), nullable=False, server_default=""),
)


def init_schema(engine: sa.Engine) -> None:
    """Create the tables if missing and seed any missing definitions and version 1 defaults.

    Safe to run repeatedly: existing rows are left alone.
    """
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        have_definitions = set(conn.scalars(sa.select(param_definitions.c.param_key)))
        have_versions = set(conn.scalars(sa.select(param_versions.c.param_key)))
        for d in model.DEFINITIONS:
            if d.key not in have_definitions:
                conn.execute(
                    param_definitions.insert().values(
                        param_key=d.key,
                        data_type=d.data_type,
                        min_value=model.to_text(d.min_value),
                        max_value=model.to_text(d.max_value),
                        unit=d.unit,
                        description=d.description,
                    )
                )
            if d.key not in have_versions:
                conn.execute(
                    param_versions.insert().values(
                        param_key=d.key,
                        version=1,
                        value_text=model.to_text(d.default),
                        effective_from=SEED_EFFECTIVE_FROM,
                        updated_by="seed",
                        updated_at=now,
                        change_note="initial default",
                    )
                )
