"""Parameter store on Delta tables in `ops`, used from jobs with Spark SQL (spec section 4.6).

Minimal version: what the ETL needs (create and seed the tables, read as of a date) plus
`save` and `history` so it satisfies `ParamRepository`. Rows are read into Python and picked
with the shared model code, so the as-of rule is the same as on the SQL backends.

Delta does not enforce primary keys. `save` inserts all changed keys in one atomic statement,
but two simultaneous saves could pick the same version number. The app is the only writer
and serialises its saves, so this is accepted for the PoC.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from bakehouse_core.io.ops_tables import ident
from bakehouse_core.params import model
from bakehouse_core.params.repository import ParamRepository, ParamValidationError, ParamVersion
from bakehouse_core.params.schema import PARAMS_SCHEMA, SEED_EFFECTIVE_FROM


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _as_datetime(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


class DeltaRepository(ParamRepository):
    def __init__(self, spark: Any, catalog: str, schema: str = "ops"):
        self._spark = spark
        self._catalog = ident(catalog)
        self._schema = ident(schema)

    def _t(self, table: str) -> str:
        return f"{self._catalog}.{self._schema}.{ident(table)}"

    def _sql(self, query: str, **args: Any) -> Any:
        return self._spark.sql(query, args=args) if args else self._spark.sql(query)

    def _keys(self, table: str) -> set[str]:
        return {r[0] for r in self._sql(f"SELECT param_key FROM {self._t(table)}").collect()}

    def ensure_schema(self) -> None:
        self.ensure_tables()

    def ensure_tables(self) -> None:
        """Create the tables if missing and seed any missing definitions and version 1 defaults."""
        self._sql(
            f"CREATE TABLE IF NOT EXISTS {self._t('param_definitions')} "
            "(param_key STRING, data_type STRING, min_value STRING, max_value STRING, "
            "unit STRING, description STRING)"
        )
        self._sql(
            f"CREATE TABLE IF NOT EXISTS {self._t('param_versions')} "
            "(param_key STRING, version INT, value_text STRING, effective_from DATE, "
            "updated_by STRING, updated_at TIMESTAMP, change_note STRING)"
        )
        have_defs = {
            r[0]
            for r in self._sql(f"SELECT param_key FROM {self._t('param_definitions')}").collect()
        }
        have_versions = {
            r[0] for r in self._sql(f"SELECT param_key FROM {self._t('param_versions')}").collect()
        }
        missing_defs = [d for d in model.DEFINITIONS if d.key not in have_defs]
        if missing_defs:
            args: dict[str, str] = {}
            rows = []
            for i, d in enumerate(missing_defs):
                args.update(
                    {
                        f"k{i}": d.key,
                        f"t{i}": d.data_type,
                        f"lo{i}": model.to_text(d.min_value),
                        f"hi{i}": model.to_text(d.max_value),
                        f"u{i}": d.unit,
                        f"d{i}": d.description,
                    }
                )
                rows.append(f"(:k{i}, :t{i}, :lo{i}, :hi{i}, :u{i}, :d{i})")
            self._sql(
                f"INSERT INTO {self._t('param_definitions')} VALUES " + ", ".join(rows), **args
            )
        missing_versions = [d for d in model.DEFINITIONS if d.key not in have_versions]
        if missing_versions:
            rows = []
            args = {
                "eff": SEED_EFFECTIVE_FROM.isoformat(),
                "now": datetime.now(UTC).isoformat(),
            }
            for i, d in enumerate(missing_versions):
                args[f"k{i}"] = d.key
                args[f"v{i}"] = model.to_text(d.default)
                rows.append(
                    f"(:k{i}, 1, :v{i}, CAST(:eff AS DATE), 'seed', "
                    "CAST(:now AS TIMESTAMP), 'initial default')"
                )
            self._sql(f"INSERT INTO {self._t('param_versions')} VALUES " + ", ".join(rows), **args)

    def _load(self, as_of: date | None) -> dict[str, tuple[int, str]]:
        query = f"SELECT param_key, version, value_text FROM {self._t('param_versions')}"
        args: dict[str, str] = {}
        if as_of is not None:
            query += " WHERE effective_from <= CAST(:as_of AS DATE)"
            args["as_of"] = as_of.isoformat()
        rows = sorted(self._sql(query, **args).collect(), key=lambda r: (r[0], int(r[1])))
        # Ordered by version, so the last row seen for a key is the highest version.
        return {key: (int(version), text) for key, version, text in rows}

    def load_current(self) -> dict[str, model.Value]:
        return {k: model.from_text(k, text) for k, (_v, text) in self._load(None).items()}

    def load_as_of(self, as_of: date) -> dict[str, model.Value]:
        return {k: model.from_text(k, text) for k, (_v, text) in self._load(as_of).items()}

    def versions_as_of(self, as_of: date) -> dict[str, int]:
        return {k: v for k, (v, _text) in self._load(as_of).items()}

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
        tops = {
            r[0]: int(r[1])
            for r in self._sql(
                f"SELECT param_key, MAX(version) FROM {self._t('param_versions')} "
                "GROUP BY param_key"
            ).collect()
        }
        now = datetime.now(UTC)
        args: dict[str, str] = {
            "eff": (effective_from or now.date()).isoformat(),
            "now": now.isoformat(),
            "by": user,
            "note": note,
        }
        rows = []
        new_versions: dict[str, int] = {}
        for i, (key, value) in enumerate(parsed.items()):
            version = tops.get(key, 0) + 1
            new_versions[key] = version
            args[f"k{i}"] = key
            args[f"v{i}"] = model.to_text(value)
            rows.append(
                f"(:k{i}, {version}, :v{i}, CAST(:eff AS DATE), :by, "
                "CAST(:now AS TIMESTAMP), :note)"
            )
        self._sql(f"INSERT INTO {self._t('param_versions')} VALUES " + ", ".join(rows), **args)
        return new_versions

    def history(self, key: str) -> list[ParamVersion]:
        rows = self._sql(
            "SELECT param_key, version, value_text, effective_from, updated_by, updated_at, "
            f"change_note FROM {self._t('param_versions')} WHERE param_key = :k",
            k=key,
        ).collect()
        return [
            ParamVersion(
                key=r[0],
                version=int(r[1]),
                value=model.from_text(r[0], r[2]),
                effective_from=_as_date(r[3]),
                updated_by=r[4],
                updated_at=_as_datetime(r[5]),
                change_note=r[6],
            )
            for r in sorted(rows, key=lambda r: int(r[1]))
        ]


class CatalogReadRepository(DeltaRepository):
    """Read-only access to the parameters held in Lakebase, through its Unity Catalog catalog.

    The ETL reads them with Spark SQL, as spec section 4.5 describes. Only the app writes to
    Lakebase, and it creates the tables, so setting up and saving are refused here.
    """

    def __init__(self, spark: Any, catalog: str, schema: str = PARAMS_SCHEMA):
        super().__init__(spark, catalog, schema)

    def ensure_schema(self) -> None:
        """Nothing to create: the app owns the tables."""

    def ensure_tables(self) -> None:
        """Nothing to create: the app owns the tables."""

    def save(self, *args: Any, **kwargs: Any) -> dict[str, int]:
        raise PermissionError("the Lakebase catalog is read-only here; save through the app")
