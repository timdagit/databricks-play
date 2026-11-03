"""Reading and saving parameters (spec section 4.3)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

import sqlalchemy as sa

from bakehouse_core.params import model
from bakehouse_core.params.schema import param_versions


class ParamValidationError(ValueError):
    """Raised by `save` when changes break a limit or a cross-field rule."""

    def __init__(self, errors: Mapping[str, str]):
        self.errors = dict(errors)
        super().__init__("; ".join(f"{k}: {v}" for k, v in self.errors.items()))


@dataclass(frozen=True)
class ParamVersion:
    key: str
    version: int
    value: model.Value
    effective_from: date
    updated_by: str
    updated_at: datetime
    change_note: str


class ParamRepository(ABC):
    """What the ETL, the app and the tests need from a parameter store."""

    @abstractmethod
    def load_current(self) -> dict[str, model.Value]:
        """The highest version of every parameter."""

    @abstractmethod
    def load_as_of(self, as_of: date) -> dict[str, model.Value]:
        """The highest version of every parameter with `effective_from <= as_of`."""

    @abstractmethod
    def save(
        self,
        changes: Mapping[str, object],
        user: str,
        note: str = "",
        effective_from: date | None = None,
    ) -> dict[str, int]:
        """Validate and insert one new version per changed key. Returns the new versions.

        `effective_from` defaults to today. Raises `ParamValidationError` and saves
        nothing if any change is invalid.
        """

    @abstractmethod
    def history(self, key: str) -> list[ParamVersion]:
        """Every version of one parameter, oldest first."""


class SqlRepository(ParamRepository):
    """Parameter store on any SQLAlchemy database (SQLite, Postgres, Lakebase)."""

    def __init__(self, engine: sa.Engine):
        self._engine = engine

    def _load(self, as_of: date | None) -> dict[str, model.Value]:
        query = sa.select(
            param_versions.c.param_key, param_versions.c.version, param_versions.c.value_text
        ).order_by(param_versions.c.param_key, param_versions.c.version)
        if as_of is not None:
            query = query.where(param_versions.c.effective_from <= as_of)
        latest: dict[str, model.Value] = {}
        with self._engine.connect() as conn:
            # Ordered by version, so the last row seen for a key is the highest version.
            for key, _version, text in conn.execute(query):
                latest[key] = model.from_text(key, text)
        return latest

    def load_current(self) -> dict[str, model.Value]:
        return self._load(None)

    def load_as_of(self, as_of: date) -> dict[str, model.Value]:
        return self._load(as_of)

    def save(
        self,
        changes: Mapping[str, object],
        user: str,
        note: str = "",
        effective_from: date | None = None,
    ) -> dict[str, int]:
        parsed, errors = model.validate_changes(self.load_current(), changes)
        if errors:
            raise ParamValidationError(errors)
        if not parsed:
            return {}
        effective = effective_from or datetime.now(UTC).date()
        now = datetime.now(UTC)
        new_versions: dict[str, int] = {}
        # One transaction, so a multi-parameter save is all or nothing. If two saves race
        # for the same version, the primary key rejects the second one.
        with self._engine.begin() as conn:
            for key, value in parsed.items():
                top = conn.scalar(
                    sa.select(sa.func.max(param_versions.c.version)).where(
                        param_versions.c.param_key == key
                    )
                )
                version = (top or 0) + 1
                conn.execute(
                    param_versions.insert().values(
                        param_key=key,
                        version=version,
                        value_text=model.to_text(value),
                        effective_from=effective,
                        updated_by=user,
                        updated_at=now,
                        change_note=note,
                    )
                )
                new_versions[key] = version
        return new_versions

    def history(self, key: str) -> list[ParamVersion]:
        query = (
            sa.select(param_versions)
            .where(param_versions.c.param_key == key)
            .order_by(param_versions.c.version)
        )
        with self._engine.connect() as conn:
            return [
                ParamVersion(
                    key=row.param_key,
                    version=row.version,
                    value=model.from_text(row.param_key, row.value_text),
                    effective_from=row.effective_from,
                    updated_by=row.updated_by,
                    updated_at=row.updated_at,
                    change_note=row.change_note,
                )
                for row in conn.execute(query)
            ]
