import asyncio

import bcrypt
import pytest
from flask import Request
from graphql import get_introspection_query
from werkzeug.test import EnvironBuilder

from app import create_app
from app.auth import hash_password, verify_password
from app.rate_limit import MemoryRateLimiter, RateLimitExceeded
from app.schema import schema


@pytest.mark.parametrize("password", ["", " " * 12, "short", "a" * 129, "\ud800" * 12])
def test_reject_unsupported_new_passwords(password: str) -> None:
    with pytest.raises(ValueError):
        hash_password(password)


@pytest.mark.parametrize("password", ["a" * 72 + "first", "🌱" * 24, "a" * 128])
def test_entire_password_is_verified(password: str) -> None:
    encoded = hash_password(password)
    assert encoded.startswith("scrypt:")
    assert verify_password(password, encoded)
    assert not verify_password(password[:-1] + "X", encoded)


def test_old_bcrypt_hashes_do_not_accept_truncated_input() -> None:
    short = bcrypt.hashpw(b"old-password", bcrypt.gensalt()).decode()
    assert verify_password("old-password", short)
    assert not verify_password("wrong", short)
    ambiguous = bcrypt.hashpw(b"a" * 72, bcrypt.gensalt()).decode()
    assert not verify_password("a" * 72 + "suffix", ambiguous)
    assert not verify_password("", ambiguous)
    assert not verify_password("some-password", "invalid-hash")


@pytest.mark.asyncio
async def test_memory_limit_is_atomic_and_recovers_at_window_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [120.0]
    monkeypatch.setattr("app.rate_limit.time.time", lambda: now[0])
    limiter = MemoryRateLimiter()

    async def consume() -> bool:
        try:
            await limiter.consume("test", "client", 3)
            return True
        except RateLimitExceeded:
            return False

    assert sum(await asyncio.gather(*(consume() for _ in range(10)))) == 3
    await limiter.consume("test", "other-client", 3)
    now[0] = 180.0
    assert await consume()


@pytest.mark.asyncio
async def test_graphql_limits_reject_alias_and_token_floods() -> None:
    aliases = "{ " + " ".join(f"a{i}: hello" for i in range(11)) + " }"
    result = await schema.execute(aliases)
    assert result.errors
    tokens = "{ " + " ".join("hello" for _ in range(2100)) + " }"
    result = await schema.execute(tokens)
    assert result.errors


@pytest.mark.asyncio
async def test_standard_introspection_still_works() -> None:
    result = await schema.execute(get_introspection_query())
    assert result.errors is None


def test_http_rate_limit_returns_429_and_cannot_be_spoofed() -> None:
    app = create_app(init_database=False)
    client = app.test_client()
    for i in range(120):
        response = client.post(
            "/graphql",
            json={"query": "{ hello }"},
            headers={"X-Forwarded-For": f"192.0.2.{i}"},
        )
        assert response.status_code == 200
    blocked = client.post("/graphql", json={"query": "{ hello }"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    other = client.post(
        "/graphql",
        json={"query": "{ hello }"},
        environ_overrides={"REMOTE_ADDR": "192.0.2.200"},
    )
    assert other.status_code == 200


def test_http_body_limit_returns_413() -> None:
    app = create_app(init_database=False)
    response = app.test_client().post("/graphql", data=b"x" * (64 * 1024 + 1))
    assert response.status_code == 413
    assert response.is_json


@pytest.mark.asyncio
async def test_auth_aliases_are_counted_as_separate_attempts() -> None:
    request = Request(EnvironBuilder(path="/graphql").get_environ())
    context = {"request": request, "rate_limiter": MemoryRateLimiter()}
    # Invalid credentials are rejected before database access, but still consume the limit.
    query = (
        "mutation { "
        + " ".join(f'a{i}: login(email:"", password:"") {{ __typename }}' for i in range(6))
        + " }"
    )
    result = await schema.execute(query, context_value=context)
    assert result.errors
    assert result.errors[0].extensions is not None
    assert result.errors[0].extensions["code"] == "RATE_LIMITED"


def test_trusted_proxy_setting_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    client = create_app(init_database=False).test_client()
    for i in range(121):
        response = client.post(
            "/graphql",
            json={"query": "{ hello }"},
            headers={"X-Forwarded-For": f"192.0.2.{i}"},
        )
        assert response.status_code == 200
