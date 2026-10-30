from datetime import UTC, datetime
from typing import Annotated

from beanie import Document, Indexed, Link, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.models.user import User


class Comment(Document):
    """Comments live in their own collection, not embedded in Recipe:
    they grow unboundedly and are written independently of the parent.

    recipe_id is a plain indexed id, not Link[Recipe], on purpose — it is
    only ever queried the *other* way round ("all comments for these N
    recipes"), which is one indexed find(), whereas a Link would suggest
    forward-fetching the recipe we already have.
    """

    recipe_id: Annotated[PydanticObjectId, Indexed()]
    author: Link[User]
    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "comments"
        indexes = [IndexModel([("recipe_id", 1), ("_id", 1)])]
