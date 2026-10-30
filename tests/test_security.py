import asyncio
import os
from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any, cast

import httpx
import pytest
import pytest_asyncio
from asgiref.wsgi import WsgiToAsgi
from beanie import PydanticObjectId, init_beanie
from pymongo import AsyncMongoClient

from app import create_app
from app.models.comment import Comment
from app.models.recipe import Ingredient, Recipe, Step
from app.models.user import User
from app.rate_limit import MongoRateLimiter, RateLimitExceeded
from app.schema import schema
from app.schema.dataloaders import create_comments_loader, create_dataloaders

TEST_DB_NAME = "recipe_aggregator_test"
VALID_RECIPE: dict[str, Any] = {
    "title": "Bread",
    "description": "A simple loaf.",
    "ingredients": [{"name": "Flour", "amount": 100.0, "unit": "g"}],
    "steps": [{"order": 1, "text": "Mix."}],
    "tags": ["bread"],
}


@pytest_asyncio.fixture
async def test_db() -> AsyncIterator[None]:
    uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    client: AsyncMongoClient = AsyncMongoClient(uri, tz_aware=True)
    await init_beanie(database=client[TEST_DB_NAME], document_models=[User, Recipe, Comment])
    try:
        yield
    finally:
        await client.drop_database(TEST_DB_NAME)
        await client.close()


def context(user: User | None = None) -> dict[str, Any]:
    return {
        "current_user": user,
        "user_loader": create_dataloaders(),
        "comments_loader": create_comments_loader(),
    }


async def make_user() -> User:
    user = User(email="private@example.com", name="Owner", password_hash="unused")
    await user.insert()
    return user


async def make_recipe(user: User, title: str = "Bread") -> Recipe:
    recipe = Recipe(
        title=title,
        description="A simple loaf.",
        ingredients=[Ingredient(name="Flour", amount=100, unit="g")],
        steps=[Step(order=1, text="Mix.")],
        author=user,
    )
    await recipe.insert()
    return recipe


@pytest.mark.asyncio
async def test_author_email_is_not_public_even_to_another_logged_in_user(test_db: None) -> None:
    user = await make_user()
    recipe = await make_recipe(user)
    await Comment(recipe_id=recipe.id, author=user, text="Hello").insert()
    for current_user in (None, user):
        public = await schema.execute(
            "{ recipes { author { id name } comments { author { id name } } } }",
            context_value=context(current_user),
        )
        assert public.errors is None
        assert public.data is not None
        assert "private@example.com" not in str(public.data)
        for query in (
            "{ recipes { author { email } } }",
            "{ recipes { comments { author { email } } } }",
        ):
            private = await schema.execute(query, context_value=context(current_user))
            assert private.errors
            assert private.data is None
    me = await schema.execute("{ me { email } }", context_value=context(user))
    assert me.data == {"me": {"email": "private@example.com"}}
    guest = await schema.execute("{ me { email } }", context_value=context())
    assert guest.data == {"me": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"password": ""},
        {"password": "short"},
        {"password": " " * 12},
        {"password": "x" * 129},
        {"name": "  "},
        {"email": "not-an-email"},
    ],
)
async def test_invalid_registration_does_not_create_user(
    test_db: None,
    overrides: dict[str, str],
) -> None:
    values = {"email": "private@example.com", "password": "correct-horse-battery", "name": "Owner"}
    values.update(overrides)
    result = await schema.execute(
        """mutation($email:String!, $password:String!, $name:String!) {
            register(email:$email, password:$password, name:$name) {
                __typename ... on AuthError { message }
            }
        }""",
        variable_values=values,
    )
    assert result.errors is None
    assert result.data is not None
    assert result.data["register"]["__typename"] == "AuthError"
    assert await User.count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"description": ""},
        {"ingredients": []},
        {"steps": []},
        {"ingredients": [{"name": "Flour", "amount": -1, "unit": "g"}]},
        {"ingredients": [{"name": "Flour", "amount": 0, "unit": "g"}]},
        {"ingredients": [{"name": "", "amount": 1, "unit": "g"}]},
        {"ingredients": [{"name": "Flour", "amount": 1, "unit": ""}]},
        {"steps": [{"order": 0, "text": "Mix"}]},
        {"steps": [{"order": 2, "text": "Mix"}]},
        {"steps": [{"order": 1, "text": ""}]},
        {"tags": ["bread", "BREAD"]},
        {"tags": [""]},
        {"title": "x" * 201},
        {"description": "x" * 5001},
        {"ingredients": [{"name": "Flour", "amount": 1, "unit": "g"}] * 51},
    ],
)
async def test_invalid_recipe_input_is_rejected_without_inserting(
    test_db: None,
    changes: dict[str, Any],
) -> None:
    user = await make_user()
    values = deepcopy(VALID_RECIPE)
    values.update(changes)
    result = await schema.execute(
        "mutation($input:CreateRecipeInput!) { createRecipe(input:$input) { id } }",
        variable_values={"input": values},
        context_value=context(user),
    )
    assert result.errors
    assert result.errors[0].extensions is not None
    assert result.errors[0].extensions["code"] == "BAD_USER_INPUT"
    assert await Recipe.count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
async def test_invalid_comments_are_rejected(test_db: None, text: str) -> None:
    user = await make_user()
    recipe = await make_recipe(user)
    result = await schema.execute(
        """mutation($id:ID!, $text:String!) {
            addComment(recipeId:$id, text:$text) { __typename }
        }""",
        variable_values={"id": str(recipe.id), "text": text},
        context_value=context(user),
    )
    assert result.errors is None
    assert result.data == {"addComment": {"__typename": "CommentError"}}
    assert await Comment.count() == 0


@pytest.mark.asyncio
async def test_recipe_and_favorite_pages_are_stable_and_bounded(test_db: None) -> None:
    user = await make_user()
    recipes = [await make_recipe(user, f"Recipe {i:02}") for i in range(25)]
    user.favorite_recipe_ids = [cast(PydanticObjectId, r.id) for r in recipes]
    await user.save()
    for field in ("recipes", "myFavorites"):
        default = await schema.execute(f"{{ {field} {{ title }} }}", context_value=context(user))
        assert default.errors is None and default.data is not None
        assert len(default.data[field]) == 20
        page = await schema.execute(
            f"{{ {field}(limit:5, offset:5) {{ title }} }}",
            context_value=context(user),
        )
        assert page.errors is None and page.data is not None
        assert [r["title"] for r in page.data[field]] == [f"Recipe {i:02}" for i in range(5, 10)]
        for args in ("limit:0", "limit:51", "offset:-1", "offset:10001"):
            invalid = await schema.execute(
                f"{{ {field}({args}) {{ title }} }}",
                context_value=context(user),
            )
            assert invalid.errors
            assert invalid.errors[0].extensions is not None
            assert invalid.errors[0].extensions["code"] == "BAD_USER_INPUT"


@pytest.mark.asyncio
async def test_comments_are_paged_per_recipe_and_per_alias(test_db: None) -> None:
    user = await make_user()
    first = await make_recipe(user, "First")
    second = await make_recipe(user, "Second")
    for recipe in (first, second):
        for i in range(23):
            await Comment(recipe_id=recipe.id, author=user, text=f"{recipe.title} {i}").insert()
    result = await schema.execute(
        """{ recipes {
            title comments { text }
            later:comments(limit:2, offset:11) { text }
        } }""",
        context_value=context(),
    )
    assert result.errors is None and result.data is not None
    for recipe in result.data["recipes"]:
        assert len(recipe["comments"]) == 10
        assert [c["text"] for c in recipe["later"]] == [
            f"{recipe['title']} 11",
            f"{recipe['title']} 12",
        ]
    invalid = await schema.execute(
        "{ recipes { comments(limit:21) { text } } }",
        context_value=context(),
    )
    assert invalid.errors


@pytest.mark.asyncio
async def test_mongo_limits_are_shared_and_atomic(test_db: None) -> None:
    one, two = MongoRateLimiter(), MongoRateLimiter()

    async def consume(limiter: MongoRateLimiter) -> bool:
        try:
            await limiter.consume("shared", "ip", 5)
            return True
        except RateLimitExceeded:
            return False

    assert sum(await asyncio.gather(*(consume(one if i % 2 else two) for i in range(15)))) == 5
    await two.consume("shared", "other-ip", 5)


@pytest.mark.asyncio
async def test_http_login_limit_and_account_privacy(test_db: None) -> None:
    app = WsgiToAsgi(create_app(init_database=False, rate_limiter=MongoRateLimiter()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        register = await client.post(
            "/graphql",
            json={
                "query": """mutation {
            register(email:"person@example.com", password:"correct-horse-battery", name:"Person") {
                ... on AuthPayload { token user { email } }
            }
        }"""
            },
        )
        data = register.json()["data"]["register"]
        assert data["user"]["email"] == "person@example.com"
        # Registration and login share the same per-IP/account attempt budget.
        for _ in range(4):
            response = await client.post(
                "/graphql",
                json={
                    "query": """mutation {
                login(email:"person@example.com", password:"wrong") { __typename }
            }"""
                },
            )
            assert response.json()["data"]["login"]["__typename"] == "AuthError"
        response = await client.post(
            "/graphql",
            json={
                "query": """mutation {
            login(email:"person@example.com", password:"correct-horse-battery") { __typename }
        }"""
            },
        )
        error = response.json()["errors"][0]
        assert error["extensions"]["code"] == "RATE_LIMITED"
        assert error["extensions"]["retryAfter"] > 0


@pytest.mark.asyncio
async def test_cold_start_initializes_shared_counters_and_ttl(
    test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib.parse import urlsplit, urlunsplit

    uri = urlsplit(os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017"))
    monkeypatch.setenv("MONGO_URI", urlunsplit(uri._replace(path=f"/{TEST_DB_NAME}")))
    app = WsgiToAsgi(create_app())
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(2):
                response = await client.post("/graphql", json={"query": "{ hello }"})
                assert response.json() == {"data": {"hello": "world"}}
            assert (await client.get("/")).status_code == 200
        collection = User.get_pymongo_collection().database["api_rate_limits"]
        indexes = await collection.index_information()
        assert indexes["expires_at_1"]["expireAfterSeconds"] == 0
        assert await collection.count_documents({}) == 1
    finally:
        await User.get_pymongo_collection().database.client.close()
