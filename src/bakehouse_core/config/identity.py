"""Small interfaces that keep auth and identity out of the parameter code.

The Databricks implementations (Apps OAuth, CLI profile, managed identity) arrive with the
app workstream. What is here is enough for local development and tests.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

# Header Databricks Apps sets to the signed-in user (confirmed on the deployed app).
FORWARDED_EMAIL_HEADER = "X-Forwarded-Email"


class TokenProvider(Protocol):
    """Supplies a fresh access token each time a connection is opened."""

    def get_token(self) -> str: ...


class StaticTokenProvider:
    """A fixed token or password, e.g. the local Postgres container's."""

    def __init__(self, token: str):
        self._token = token

    def get_token(self) -> str:
        return self._token

    @classmethod
    def from_env(cls, name: str = "PGPASSWORD") -> StaticTokenProvider:
        token = os.environ.get(name)
        if token is None:
            raise RuntimeError(f"environment variable {name} is not set")
        return cls(token)


# Returns the name to record in `updated_by`.
UserResolver = Callable[[], str]


def fixed_user(name: str) -> UserResolver:
    """Always the same user, for local runs and jobs."""
    return lambda: name


def user_from_headers(headers: Mapping[str, str], fallback: str = "unknown") -> str:
    """The signed-in user from request headers, or `fallback` when absent."""
    lowered = {k.lower(): v for k, v in headers.items()}
    return lowered.get(FORWARDED_EMAIL_HEADER.lower()) or fallback


def _as_datetime(value: object) -> datetime | None:
    """A timezone-aware datetime from what the SDK returns, or None if there is no expiry.

    The SDK gives a protobuf `Timestamp` (which has `ToDatetime`). A plain `datetime` is
    accepted too, and a naive one is taken as UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    to_datetime = getattr(value, "ToDatetime", None)
    if callable(to_datetime):
        return to_datetime(tzinfo=UTC)
    return None


class LakebaseTokenProvider:
    """A Lakebase login token from the Databricks SDK, reused until shortly before it expires.

    `client` is a `databricks.sdk.WorkspaceClient`, or anything with the same
    `postgres.generate_database_credential(endpoint=...)` call. `endpoint` is the compute's
    resource name: `projects/<project>/branches/<branch>/endpoints/<endpoint>`. Tokens last up
    to an hour, so a cached one saves an API call on every new connection.
    """

    REFRESH_BEFORE_SECONDS = 120
    FALLBACK_LIFETIME_SECONDS = 600  # if the response carries no expiry

    def __init__(
        self,
        client: object,
        endpoint: str,
        clock: Callable[[], datetime] | None = None,
    ):
        self._client = client
        self._endpoint = endpoint
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token: str | None = None
        self._refresh_at: datetime | None = None

    def get_token(self) -> str:
        now = self._clock()
        if self._token is None or self._refresh_at is None or now >= self._refresh_at:
            credential = self._client.postgres.generate_database_credential(endpoint=self._endpoint)
            if not credential.token:
                raise RuntimeError("Lakebase returned no token")
            expires = _as_datetime(credential.expire_time) or now + timedelta(
                seconds=self.FALLBACK_LIFETIME_SECONDS
            )
            self._token = credential.token
            self._refresh_at = expires - timedelta(seconds=self.REFRESH_BEFORE_SECONDS)
        return self._token
