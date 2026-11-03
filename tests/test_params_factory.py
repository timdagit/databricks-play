import pytest
import sqlalchemy as sa

from bakehouse_core.config.identity import (
    StaticTokenProvider,
    fixed_user,
    user_from_headers,
)
from bakehouse_core.jobs.trigger import FakeJobTrigger
from bakehouse_core.params import factory, model
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


def test_delta_is_not_built_yet():
    with pytest.raises(NotImplementedError):
        factory.get_repository({"PARAM_BACKEND": "delta"})


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
