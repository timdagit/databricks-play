from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bakehouse_core.config.identity import (
    LakebaseTokenProvider,
    StaticTokenProvider,
    fixed_user,
    user_from_headers,
)
from bakehouse_core.jobs.trigger import FakeJobTrigger
from bakehouse_core.params import factory, model
from bakehouse_core.params.delta import CatalogReadRepository, DeltaRepository
from bakehouse_core.params.repository import SqlRepository
from bakehouse_core.params.schema import init_schema


@pytest.fixture
def captured(monkeypatch):
    """Replace create_engine so no database driver is needed, and record what it was given."""
    calls = []
    real_create_engine = sa.create_engine

    def fake_create_engine(url, **kwargs):
        calls.append((url, kwargs))
        return real_create_engine("sqlite://")

    monkeypatch.setattr(factory.sa, "create_engine", fake_create_engine)
    return calls


def test_sqlite_defaults_to_in_memory():
    repo = factory.get_repository({"PARAM_BACKEND": "sqlite"})
    assert isinstance(repo, SqlRepository)
    init_schema(repo._engine)
    assert repo.load_current() == model.defaults()


def test_backend_name_is_case_insensitive():
    assert isinstance(factory.get_repository({"PARAM_BACKEND": "SQLite"}), SqlRepository)


def test_postgres_uses_database_url(captured):
    factory.get_repository({"PARAM_BACKEND": "postgres", "DATABASE_URL": "postgresql://h/db"})
    assert captured[0][0] == "postgresql://h/db"


def test_postgres_requires_database_url():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        factory.get_repository({"PARAM_BACKEND": "postgres"})


def test_lakebase_builds_url_from_pg_variables(captured):
    env = {"PARAM_BACKEND": "lakebase", "PGHOST": "db.example", "PGDATABASE": "d", "PGUSER": "u"}
    factory.get_repository(env, token_provider=StaticTokenProvider("t"))
    url = captured[0][0]
    assert (url.host, url.database, url.username, url.port) == ("db.example", "d", "u", 5432)
    assert url.query["sslmode"] == "require"


def test_lakebase_needs_a_token_provider():
    env = {"PARAM_BACKEND": "lakebase", "PGHOST": "h", "PGDATABASE": "d", "PGUSER": "u"}
    with pytest.raises(ValueError, match="token_provider"):
        factory.get_repository(env)


def test_lakebase_requires_connection_variables():
    with pytest.raises(ValueError, match="PGHOST"):
        factory.get_repository(
            {"PARAM_BACKEND": "lakebase", "PGDATABASE": "d", "PGUSER": "u"},
            token_provider=StaticTokenProvider("t"),
        )


def test_token_provider_is_asked_on_every_connect():
    tokens = iter(["first", "second"])

    class Rotating:
        def get_token(self):
            return next(tokens)

    engine = sa.create_engine("sqlite://")
    factory.attach_token_auth(engine, Rotating())
    seen = []

    # Registered after the provider's handler, so it sees the password that handler set.
    @sa.event.listens_for(engine, "do_connect")
    def _spy(dialect, conn_rec, cargs, cparams):
        seen.append(cparams.pop("password"))  # sqlite3 does not accept a password

    for _ in range(2):
        engine.dispose()
        with engine.connect():
            pass
    assert seen == ["first", "second"]


def test_delta_needs_spark_and_catalog():
    with pytest.raises(ValueError, match="spark session and a catalog"):
        factory.get_repository({"PARAM_BACKEND": "delta"})


def test_delta_backend_builds_a_delta_repository():
    repo = factory.get_repository({"PARAM_BACKEND": "delta"}, spark=object(), catalog="c")
    assert isinstance(repo, DeltaRepository)


@pytest.mark.parametrize("value", ["", "mysql"])
def test_unknown_backend_is_rejected(value):
    with pytest.raises(ValueError, match="PARAM_BACKEND"):
        factory.get_repository({"PARAM_BACKEND": value})


def test_static_token_from_env(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "secret")
    assert StaticTokenProvider.from_env().get_token() == "secret"
    monkeypatch.delenv("PGPASSWORD")
    with pytest.raises(RuntimeError, match="PGPASSWORD"):
        StaticTokenProvider.from_env()


def test_user_resolvers():
    assert fixed_user("tim")() == "tim"
    assert user_from_headers({"x-forwarded-email": "a@b.c"}) == "a@b.c"
    assert user_from_headers({}) == "unknown"
    assert user_from_headers({}, fallback="local") == "local"


def test_fake_job_trigger_records_calls():
    trigger = FakeJobTrigger()
    assert trigger.trigger("daily_run", {"triggered_by": "app"}) == "fake-run-1"
    assert trigger.calls == [("daily_run", {"triggered_by": "app"})]


def test_in_memory_sqlite_is_shared_between_threads():
    import threading

    repo = factory.get_repository({"PARAM_BACKEND": "sqlite"})
    repo.ensure_schema()
    seen = {}

    def read():
        seen["values"] = repo.load_current()

    thread = threading.Thread(target=read)
    thread.start()
    thread.join()
    assert seen["values"] == model.defaults()


def test_jobs_pick_delta_or_the_lakebase_catalog():
    assert isinstance(factory.spark_repository(object(), "delta", "c"), DeltaRepository)
    lakebase = factory.spark_repository(object(), "lakebase", "c", "params_c")
    assert isinstance(lakebase, CatalogReadRepository)


def test_lakebase_for_jobs_needs_its_catalog_and_other_names_are_rejected():
    with pytest.raises(ValueError, match="registered as"):
        factory.spark_repository(object(), "lakebase", "c")
    with pytest.raises(ValueError, match="'delta' or 'lakebase'"):
        factory.spark_repository(object(), "sqlite", "c")


START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ENDPOINT = "projects/p/branches/b/endpoints/e"


class FakeCredentialApi:
    """Stands in for the SDK client: `client.postgres.generate_database_credential`."""

    def __init__(self, with_expiry=True):
        self.calls = []
        self._with_expiry = with_expiry
        self.postgres = self

    def generate_database_credential(self, endpoint):
        self.calls.append(endpoint)
        expires = START + timedelta(hours=1) if self._with_expiry else None
        return SimpleNamespace(token=f"token-{len(self.calls)}", expire_time=expires)


def make_provider(client):
    clock = {"now": START}
    return LakebaseTokenProvider(client, ENDPOINT, lambda: clock["now"]), clock


def test_lakebase_token_is_reused_until_close_to_expiry():
    client = FakeCredentialApi()
    provider, clock = make_provider(client)
    assert provider.get_token() == "token-1"
    clock["now"] = START + timedelta(minutes=57)  # still more than 2 minutes left
    assert provider.get_token() == "token-1"
    assert client.calls == [ENDPOINT]
    clock["now"] = START + timedelta(minutes=59)  # inside the refresh margin
    assert provider.get_token() == "token-2"


def test_lakebase_token_without_expiry_is_refreshed_after_the_fallback_lifetime():
    provider, clock = make_provider(FakeCredentialApi(with_expiry=False))
    provider.get_token()
    clock["now"] = START + timedelta(minutes=5)
    assert provider.get_token() == "token-1"
    clock["now"] = START + timedelta(minutes=9, seconds=30)
    assert provider.get_token() == "token-2"


def test_lakebase_response_without_a_token_is_an_error():
    client = SimpleNamespace(
        postgres=SimpleNamespace(
            generate_database_credential=lambda endpoint: SimpleNamespace(
                token=None, expire_time=None
            )
        )
    )
    provider, _ = make_provider(client)
    with pytest.raises(RuntimeError, match="no token"):
        provider.get_token()


def test_lakebase_token_accepts_the_protobuf_timestamp_the_sdk_returns():
    from google.protobuf.timestamp_pb2 import Timestamp

    expires = Timestamp()
    expires.FromDatetime(START + timedelta(hours=1))
    client = SimpleNamespace(
        postgres=SimpleNamespace(
            generate_database_credential=lambda endpoint: SimpleNamespace(
                token="t", expire_time=expires
            )
        )
    )
    provider, clock = make_provider(client)
    assert provider.get_token() == "t"
    clock["now"] = START + timedelta(minutes=57)
    assert provider.get_token() == "t"  # cached: the expiry was understood


def test_lakebase_token_with_an_unrecognised_expiry_type_falls_back():
    client = SimpleNamespace(
        postgres=SimpleNamespace(
            generate_database_credential=lambda endpoint: SimpleNamespace(
                token="t", expire_time="soon"
            )
        )
    )
    provider, clock = make_provider(client)
    provider.get_token()
    clock["now"] = START + timedelta(minutes=9, seconds=30)
    assert provider.get_token() == "t"  # refreshed after the fallback lifetime, no crash


def test_lakebase_token_works_with_the_sdks_own_credential_object():
    from databricks.sdk.service.postgres import DatabaseCredential

    credential = DatabaseCredential.from_dict(
        {"token": "real-token", "expire_time": "2026-11-03T13:00:00Z"}
    )
    client = SimpleNamespace(
        postgres=SimpleNamespace(generate_database_credential=lambda endpoint: credential)
    )
    provider, clock = make_provider(client)
    assert provider.get_token() == "real-token"
    clock["now"] = START + timedelta(minutes=57)
    assert provider.get_token() == "real-token"  # the real expiry (one hour out) was understood
