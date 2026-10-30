import os
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
import pytest_asyncio
from beanie import init_beanie
from pymongo import AsyncMongoClient

from app.models.comment import Comment
from app.models.recipe import Ingredient, Recipe, Step
from app.models.user import User
from app.schema import schema
from app.schema.dataloaders import create_comments_loader, create_dataloaders

os.environ.setdefault("JWT_SECRET", "test-secret")

TEST_DB_NAME = "recipe_aggregator_test"

RECIPES = """
query {
  recipes {
    id
    title
    tags
    ingredients { name amount unit }
    steps { order text }
    author { id name }
  }
}
"""

RECIPE_BY_ID = """
query Recipe($id: ID!) {
  recipe(id: $id) {
    id
    title
    author { id name }
  }
}
"""

CREATE_RECIPE = """
mutation CreateRecipe($input: CreateRecipeInput!) {
  createRecipe(input: $input) {
    id
    title
    tags
    ingredients { name amount unit }
    steps { order text }
    author { id name }
  }
}
"""


@pytest_asyncio.fixture
async def test_db() -> AsyncIterator[None]:
    uri: str = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    client: AsyncMongoClient = AsyncMongoClient(uri, tz_aware=True)
    await init_beanie(database=client[TEST_DB_NAME], document_models=[Recipe, User, Comment])
    yield
    await client.drop_database(TEST_DB_NAME)
    await client.close()


def _context(current_user: User | None = None) -> dict[str, Any]:
    """Mirrors AuthGraphQLView.get_context() — a fresh loader per execution,
    exactly as a real request would get."""
    return {
        "current_user": current_user,
        "user_loader": create_dataloaders(),
        "comments_loader": create_comments_loader(),
    }


async def _make_user(email: str = "yehor@example.com") -> User:
    user = User(email=email, password_hash="x", name="Yehor")
    await user.insert()
    return user


async def _make_recipe(author: User, title: str = "Simple Bread") -> Recipe:
    recipe = Recipe(
        title=title,
        description="A minimal bread recipe.",
        ingredients=[Ingredient(name="Flour", amount=200.0, unit="g")],
        steps=[Step(order=1, text="Mix the flour with water.")],
        tags=["bread"],
        author=author,
    )
    await recipe.insert()
    return recipe


@pytest.mark.asyncio
async def test_recipes_query_returns_recipe_with_author(test_db: None) -> None:
    author = await _make_user()
    await _make_recipe(author)

    result = await schema.execute(RECIPES, context_value=_context())

    assert result.errors is None
    assert result.data is not None
    recipes = result.data["recipes"]

    assert len(recipes) == 1
    assert recipes[0]["title"] == "Simple Bread"
    assert recipes[0]["tags"] == ["bread"]
    assert recipes[0]["ingredients"] == [{"name": "Flour", "amount": 200.0, "unit": "g"}]
    assert recipes[0]["steps"] == [{"order": 1, "text": "Mix the flour with water."}]
    # Author resolved through the DataLoader, not stored on the recipe.
    assert recipes[0]["author"] == {
        "id": str(author.id),
        "name": "Yehor",
    }


@pytest.mark.asyncio
async def test_recipe_by_id_query(test_db: None) -> None:
    author = await _make_user()
    recipe = await _make_recipe(author)

    result = await schema.execute(
        RECIPE_BY_ID,
        variable_values={"id": str(recipe.id)},
        context_value=_context(),
    )

    assert result.errors is None
    assert result.data is not None
    assert result.data["recipe"]["id"] == str(recipe.id)
    assert result.data["recipe"]["author"]["id"] == str(author.id)


@pytest.mark.asyncio
async def test_create_recipe_without_token_is_permission_error(test_db: None) -> None:
    result = await schema.execute(
        CREATE_RECIPE,
        variable_values={
            "input": {
                "title": "Anon Bread",
                "description": "Should not be created.",
                "ingredients": [{"name": "Flour", "amount": 200.0, "unit": "g"}],
                "steps": [{"order": 1, "text": "Mix."}],
                "tags": [],
            }
        },
        context_value=_context(current_user=None),
    )

    # A GraphQL error from the permission class — not a 500, not a null crash.
    assert result.errors is not None
    assert "Authentication required" in str(result.errors[0].message)
    assert await Recipe.find_all().count() == 0


@pytest.mark.asyncio
async def test_create_recipe_with_current_user(test_db: None) -> None:
    author = await _make_user()

    result = await schema.execute(
        CREATE_RECIPE,
        variable_values={
            "input": {
                "title": "Focaccia",
                "description": "Olive oil bread.",
                "ingredients": [{"name": "Flour", "amount": 500.0, "unit": "g"}],
                "steps": [{"order": 1, "text": "Knead and rest."}],
                "tags": ["bread", "italian"],
            }
        },
        context_value=_context(current_user=author),
    )

    assert result.errors is None
    assert result.data is not None
    created = result.data["createRecipe"]

    assert created["title"] == "Focaccia"
    assert created["tags"] == ["bread", "italian"]
    assert created["author"] == {
        "id": str(author.id),
        "name": "Yehor",
    }

    # Persisted, and the author link points at the logged-in user.
    stored = await Recipe.get(created["id"], fetch_links=True)
    assert stored is not None
    # fetch_links=True resolved the Link into a User document.
    assert cast(User, stored.author).id == author.id
