import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from beanie import PydanticObjectId, init_beanie
from pymongo import AsyncMongoClient
from strawberry.dataloader import DataLoader

from app.models.comment import Comment
from app.models.recipe import Ingredient, Recipe, Step
from app.models.user import User
from app.schema import schema
from app.schema.dataloaders import (
    CommentPageKey,
    create_comments_loader,
    create_dataloaders,
    load_comments_by_recipe,
)

os.environ.setdefault("JWT_SECRET", "test-secret")

TEST_DB_NAME = "recipe_aggregator_test"

ADD_COMMENT = """
mutation AddComment($recipeId: ID!, $text: String!) {
  addComment(recipeId: $recipeId, text: $text) {
    __typename
    ... on CommentPayload { comment { id text author { id name } } }
    ... on CommentError { message }
  }
}
"""

RECIPES_WITH_COMMENTS = """
query {
  recipes {
    id
    title
    comments { text author { name } }
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
    """Mirrors AuthGraphQLView.get_context(): fresh loaders per execution."""
    return {
        "current_user": current_user,
        "user_loader": create_dataloaders(),
        "comments_loader": create_comments_loader(),
    }


async def _make_user(email: str = "yehor@example.com", name: str = "Yehor") -> User:
    user = User(email=email, password_hash="x", name=name)
    await user.insert()
    return user


async def _make_recipe(author: User, title: str = "Simple Bread") -> Recipe:
    recipe = Recipe(
        title=title,
        description="A minimal bread recipe.",
        ingredients=[Ingredient(name="Flour", amount=200.0, unit="g")],
        steps=[Step(order=1, text="Mix.")],
        tags=[],
        author=author,
    )
    await recipe.insert()
    return recipe


@pytest.mark.asyncio
async def test_add_comment_succeeds_for_authenticated_user(test_db: None) -> None:
    author = await _make_user()
    recipe = await _make_recipe(author)

    result = await schema.execute(
        ADD_COMMENT,
        variable_values={"recipeId": str(recipe.id), "text": "Tasty!"},
        context_value=_context(current_user=author),
    )

    assert result.errors is None
    assert result.data is not None
    payload = result.data["addComment"]

    assert payload["__typename"] == "CommentPayload"
    assert payload["comment"]["text"] == "Tasty!"
    # Author comes from the context, not from the arguments.
    assert payload["comment"]["author"] == {
        "id": str(author.id),
        "name": "Yehor",
    }

    stored = await Comment.get(payload["comment"]["id"])
    assert stored is not None
    assert stored.recipe_id == recipe.id


@pytest.mark.asyncio
async def test_add_comment_to_missing_recipe_is_modeled_error(test_db: None) -> None:
    author = await _make_user()

    result = await schema.execute(
        ADD_COMMENT,
        variable_values={"recipeId": str(PydanticObjectId()), "text": "Hello?"},
        context_value=_context(current_user=author),
    )

    # Business error, not a server error.
    assert result.errors is None
    assert result.data is not None
    assert result.data["addComment"]["__typename"] == "CommentError"
    assert result.data["addComment"]["message"] == "Recipe not found"
    assert await Comment.find_all().count() == 0


@pytest.mark.asyncio
async def test_add_comment_without_token_is_permission_error(test_db: None) -> None:
    author = await _make_user()
    recipe = await _make_recipe(author)

    result = await schema.execute(
        ADD_COMMENT,
        variable_values={"recipeId": str(recipe.id), "text": "Anon"},
        context_value=_context(current_user=None),
    )

    assert result.errors is not None
    assert "Authentication required" in str(result.errors[0].message)
    assert await Comment.find_all().count() == 0


@pytest.mark.asyncio
async def test_comments_are_grouped_per_recipe(test_db: None) -> None:
    ann = await _make_user(email="ann@example.com", name="Ann")
    bob = await _make_user(email="bob@example.com", name="Bob")

    bread = await _make_recipe(ann, title="Bread")
    soup = await _make_recipe(bob, title="Soup")
    await _make_recipe(ann, title="Salad")  # no comments at all

    await Comment(recipe_id=bread.id, author=ann, text="First").insert()
    await Comment(recipe_id=bread.id, author=bob, text="Second").insert()
    await Comment(recipe_id=soup.id, author=ann, text="Only one").insert()

    # Count how often the batch function runs: 3 recipes must cost 1 call.
    calls = 0

    async def counting_loader(keys: list[CommentPageKey]) -> list[list[Comment]]:
        nonlocal calls
        calls += 1
        return await load_comments_by_recipe(keys)

    context = _context()
    context["comments_loader"] = DataLoader(load_fn=counting_loader)

    result = await schema.execute(RECIPES_WITH_COMMENTS, context_value=context)

    assert result.errors is None
    assert result.data is not None
    by_title = {r["title"]: r for r in result.data["recipes"]}

    assert [c["text"] for c in by_title["Bread"]["comments"]] == ["First", "Second"]
    assert [c["author"]["name"] for c in by_title["Bread"]["comments"]] == ["Ann", "Bob"]
    assert [c["text"] for c in by_title["Soup"]["comments"]] == ["Only one"]
    # Empty list, not missing/misaligned — the ordering contract.
    assert by_title["Salad"]["comments"] == []

    assert calls == 1, f"expected one batched query, got {calls}"
