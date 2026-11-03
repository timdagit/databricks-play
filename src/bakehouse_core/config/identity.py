"""Small interfaces that keep auth and identity out of the parameter code.

The Databricks implementations (Apps OAuth, CLI profile, managed identity) arrive with the
app workstream. What is here is enough for local development and tests.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Protocol

# Header Databricks Apps sets to the signed-in user. Confirm when the app is deployed.
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
