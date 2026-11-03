"""Choose a parameter backend from the environment (spec section 4.3).

`PARAM_BACKEND` is one of:
- `sqlite`: `DATABASE_URL` if set, otherwise an in-memory database.
- `postgres`: `DATABASE_URL`, e.g. the local container.
- `lakebase`: `PGHOST`, `PGDATABASE`, `PGUSER` (and optionally `PGPORT`), with a token
  provider that supplies a fresh OAuth token for each new connection.
- `delta`: Delta tables in `<catalog>.ops`, read with the `spark` session passed in.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from bakehouse_core.config.identity import TokenProvider
from bakehouse_core.params.delta import DeltaRepository
from bakehouse_core.params.repository import ParamRepository, SqlRepository

BACKENDS = ("sqlite", "postgres", "lakebase", "delta")


def attach_token_auth(engine: sa.Engine, provider: TokenProvider) -> None:
    """Use `provider`'s token as the password every time the engine opens a connection."""

    @sa.event.listens_for(engine, "do_connect")
    def _set_password(dialect, conn_rec, cargs, cparams):
        cparams["password"] = provider.get_token()


def get_repository(
    env: Mapping[str, str] | None = None,
    token_provider: TokenProvider | None = None,
    spark: Any = None,
    catalog: str | None = None,
) -> ParamRepository:
    """Build the repository named by `PARAM_BACKEND`. Does not create tables."""
    env = os.environ if env is None else env
    backend = env.get("PARAM_BACKEND", "").lower()

    if backend == "sqlite":
        url = env.get("DATABASE_URL", "sqlite://")
        if url in ("sqlite://", "sqlite:///:memory:"):
            # An in-memory database is private to one connection, and a web server uses
            # several threads, so share a single connection between them.
            return SqlRepository(
                sa.create_engine(
                    url, poolclass=sa.pool.StaticPool, connect_args={"check_same_thread": False}
                )
            )
        return SqlRepository(sa.create_engine(url))

    if backend == "postgres":
        url = _require(env, "DATABASE_URL", backend)
        return SqlRepository(sa.create_engine(url, pool_pre_ping=True))

    if backend == "lakebase":
        if token_provider is None:
            raise ValueError("the lakebase backend needs a token_provider")
        url = sa.URL.create(
            "postgresql+psycopg",
            username=_require(env, "PGUSER", backend),
            host=_require(env, "PGHOST", backend),
            port=int(env.get("PGPORT", "5432")),
            database=_require(env, "PGDATABASE", backend),
            query={"sslmode": "require"},
        )
        # Lakebase tokens expire, so recycle connections well inside the token lifetime.
        engine = sa.create_engine(url, pool_pre_ping=True, pool_recycle=1800)
        attach_token_auth(engine, token_provider)
        return SqlRepository(engine)

    if backend == "delta":
        if spark is None or not catalog:
            raise ValueError("the delta backend needs a spark session and a catalog")
        return DeltaRepository(spark, catalog)

    raise ValueError(f"PARAM_BACKEND must be one of {', '.join(BACKENDS)}, got {backend!r}")


def _require(env: Mapping[str, str], name: str, backend: str) -> str:
    value = env.get(name)
    if not value:
        raise ValueError(f"{name} is required for PARAM_BACKEND={backend}")
    return value
