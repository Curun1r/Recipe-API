import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from beanie import init_beanie
from pymongo import AsyncMongoClient

from app.auth import decode_access_token
from app.models.user import User
from app.schema import schema

os.environ.setdefault("JWT_SECRET", "test-secret")

TEST_DB_NAME = "recipe_aggregator_test"

REGISTER = """
mutation Register($email: String!, $password: String!, $name: String!) {
  register(email: $email, password: $password, name: $name) {
    __typename
    ... on AuthPayload { token user { id email name } }
    ... on AuthError { message }
  }
}
"""

LOGIN = """
mutation Login($email: String!, $password: String!) {
  login(email: $email, password: $password) {
    __typename
    ... on AuthPayload { token user { id } }
    ... on AuthError { message }
  }
}
"""


@pytest_asyncio.fixture
async def test_db() -> AsyncIterator[None]:
    """Same pattern as test_recipe_model: real Mongo, forced test DB name."""
    uri: str = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    client: AsyncMongoClient = AsyncMongoClient(uri, tz_aware=True)
    await init_beanie(database=client[TEST_DB_NAME], document_models=[User])
    yield
    await client.drop_database(TEST_DB_NAME)
    await client.close()


async def _register(email: str = "yehor@example.com") -> dict[str, Any]:
    result = await schema.execute(
        REGISTER,
        variable_values={"email": email, "password": "correct-horse-battery", "name": "Yehor"},
    )
    assert result.errors is None  # modeled errors never become GraphQL errors
    assert result.data is not None
    return result.data["register"]


@pytest.mark.asyncio
async def test_register_then_login_returns_valid_token(test_db: None) -> None:
    registered = await _register()
    assert registered["__typename"] == "AuthPayload"

    result = await schema.execute(
        LOGIN,
        variable_values={"email": "yehor@example.com", "password": "correct-horse-battery"},
    )
    assert result.errors is None
    assert result.data is not None
    login = result.data["login"]

    assert login["__typename"] == "AuthPayload"
    # Token is valid and points to the registered user.
    assert decode_access_token(login["token"]) == registered["user"]["id"]


@pytest.mark.asyncio
async def test_duplicate_email_is_modeled_error_not_500(test_db: None) -> None:
    first = await _register()
    assert first["__typename"] == "AuthPayload"

    duplicate = await _register()  # asserts errors is None inside
    assert duplicate["__typename"] == "AuthError"
    assert "already registered" in duplicate["message"]


@pytest.mark.asyncio
async def test_login_wrong_password_is_modeled_error(test_db: None) -> None:
    await _register()

    result = await schema.execute(
        LOGIN,
        variable_values={"email": "yehor@example.com", "password": "wrong"},
    )
    assert result.errors is None
    assert result.data is not None
    assert result.data["login"]["__typename"] == "AuthError"
