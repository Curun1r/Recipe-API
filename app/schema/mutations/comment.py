from typing import Annotated

import strawberry
from beanie import PydanticObjectId
from bson.errors import InvalidId
from strawberry.types import Info

from app.models.comment import Comment
from app.models.recipe import Recipe
from app.models.user import User
from app.schema.permissions import IsAuthenticated
from app.schema.types.comment import CommentType


@strawberry.type
class CommentPayload:
    comment: CommentType


@strawberry.type
class CommentError:
    message: str


# Same split as AuthResult: "recipe not found" is a business outcome the
# client renders, while "not logged in" is a permission error — the two
# are deliberately not modelled the same way.
CommentResult = Annotated[CommentPayload | CommentError, strawberry.union("CommentResult")]


@strawberry.type
class CommentMutations:
    @strawberry.mutation(permission_classes=[IsAuthenticated])
    async def add_comment(self, recipe_id: strawberry.ID, text: str, info: Info) -> CommentResult:
        text = text.strip()
        if not 1 <= len(text) <= 2000:
            return CommentError(message="Comment must contain 1 to 2000 characters")
        try:
            oid = PydanticObjectId(recipe_id)
        except (InvalidId, ValueError, TypeError):
            # Malformed id is indistinguishable from missing, for the client.
            return CommentError(message="Recipe not found")

        if await Recipe.get(oid) is None:
            return CommentError(message="Recipe not found")

        current_user: User = info.context["current_user"]
        comment = Comment(recipe_id=oid, author=current_user, text=text)
        await comment.insert()

        return CommentPayload(comment=CommentType.from_model(comment))
