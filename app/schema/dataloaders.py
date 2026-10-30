from typing import cast

from beanie import PydanticObjectId
from beanie.operators import In
from strawberry.dataloader import DataLoader

from app.models.comment import Comment
from app.models.recipe import Recipe
from app.models.user import User

UserLoader = DataLoader[PydanticObjectId, User | None]
CommentPageKey = tuple[PydanticObjectId, int, int]
CommentsByRecipeLoader = DataLoader[CommentPageKey, list[Comment]]


async def load_users(keys: list[PydanticObjectId]) -> list[User | None]:
    """Batch function: one query for N ids, instead of N queries.

    The DataLoader contract requires the result list to line up with `keys`
    positionally — Mongo returns documents in arbitrary order, so we index
    them by id and rebuild the list. Missing ids must yield None, not be
    skipped, or every later key would shift by one.
    """
    users = await User.find(In(User.id, keys)).to_list()
    # Document.id is Optional[...] because an unsaved document has none;
    # anything coming back from find() is saved, so the cast is safe.
    by_id: dict[PydanticObjectId, User] = {cast(PydanticObjectId, user.id): user for user in users}
    return [by_id.get(key) for key in keys]


async def load_comments_by_recipe(keys: list[CommentPageKey]) -> list[list[Comment]]:
    """Batch equal page windows, limiting each recipe inside MongoDB's lookup."""
    pages: dict[CommentPageKey, list[Comment]] = {}
    windows = {(limit, offset) for _, limit, offset in keys}
    for limit, offset in windows:
        ids = [oid for oid, size, start in keys if (size, start) == (limit, offset)]
        rows = await Recipe.aggregate(
            [
                {"$match": {"_id": {"$in": ids}}},
                {
                    "$lookup": {
                        "from": "comments",
                        "let": {"recipe_id": "$_id"},
                        "pipeline": [
                            {"$match": {"$expr": {"$eq": ["$recipe_id", "$$recipe_id"]}}},
                            {"$sort": {"_id": 1}},
                            {"$skip": offset},
                            {"$limit": limit},
                        ],
                        "as": "comments",
                    }
                },
                {"$project": {"_id": 1, "comments": 1}},
            ]
        ).to_list()
        for row in rows:
            pages[(row["_id"], limit, offset)] = [
                Comment.model_validate(comment) for comment in row["comments"]
            ]
    return [pages.get(key, []) for key in keys]


def create_dataloaders() -> UserLoader:
    """A fresh loader per HTTP request.

    DataLoader caches by key for its whole lifetime, so a module-level
    instance would serve one request's stale user data to another —
    and never notice updates. Cheap to create; scope it to the request.
    """
    return DataLoader(load_fn=load_users)


def create_comments_loader() -> CommentsByRecipeLoader:
    """Same per-request rule as create_dataloaders()."""
    return DataLoader(load_fn=load_comments_by_recipe)
