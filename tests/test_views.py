import os
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from asgiref.wsgi import WsgiToAsgi
from beanie import init_beanie
from pymongo import AsyncMongoClient

from app import create_app
from app.models.comment import Comment
from app.models.recipe import Ingredient, Recipe, Step
from app.models.user import User

os.environ.setdefault("JWT_SECRET", "test-secret")

TEST_DB_NAME = "recipe_aggregator_test"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Requests go through the ASGI stack, not Flask's test_client.

    Two reasons: test_client raises "You cannot use AsyncToSync in the same
    thread as an async event loop" from an async test, and WSGI would give
    each request a new event loop, which AsyncMongoClient refuses to cross.
    This is also how the app is actually served (see app/wsgi.py).
    """
    uri: str = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    mongo: AsyncMongoClient = AsyncMongoClient(uri, tz_aware=True)
    await init_beanie(database=mongo[TEST_DB_NAME], document_models=[Recipe, User, Comment])

    # init_database=False: Beanie is already initialised above, on this loop.
    asgi_app = WsgiToAsgi(create_app(init_database=False))
    transport = httpx.ASGITransport(app=asgi_app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await mongo.drop_database(TEST_DB_NAME)
    await mongo.close()


async def _make_recipe(title: str = "Focaccia") -> Recipe:
    author = User(email="yehor@example.com", password_hash="x", name="Yehor")
    await author.insert()

    recipe = Recipe(
        title=title,
        description="Olive oil bread.",
        ingredients=[Ingredient(name="Flour", amount=500.0, unit="g")],
        steps=[Step(order=1, text="Knead and rest.")],
        tags=["bread"],
        author=author,
    )
    await recipe.insert()
    return recipe


@pytest.mark.asyncio
async def test_index_returns_200(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_index_lists_recipe_with_author(client: httpx.AsyncClient) -> None:
    await _make_recipe()

    response = await client.get("/")
    html = response.text

    assert response.status_code == 200
    assert "Focaccia" in html
    # fetch_links=True resolved the author link, so the name is rendered.
    assert "Yehor" in html
    assert "bread" in html


@pytest.mark.asyncio
async def test_index_shows_friendly_empty_state(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert "No recipes yet." in response.text
    assert "<article" not in response.text


@pytest.mark.asyncio
async def test_two_consecutive_requests_share_one_event_loop(
    client: httpx.AsyncClient,
) -> None:
    """Regression: under WSGI the second DB-touching request died with
    "Cannot use AsyncMongoClient in different event loop"."""
    await _make_recipe(title="Ciabatta")

    first = await client.get("/")
    second = await client.get("/")

    assert first.status_code == 200
    assert second.status_code == 200
    assert "Ciabatta" in second.text
