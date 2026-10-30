import strawberry
from beanie.operators import In
from strawberry.types import Info

from app.models.recipe import Recipe
from app.models.user import User
from app.schema.permissions import IsAuthenticated
from app.schema.types.recipe import RecipeType
from app.schema.types.user import AccountType
from app.validation import DEFAULT_PAGE_SIZE, page_bounds


@strawberry.type
class UserQueries:
    @strawberry.field
    def me(self, info: Info) -> AccountType | None:
        """No permission class on purpose: for a guest "who am I" is
        legitimately null, not an error. Compare with myFavorites, where
        an anonymous call is a mistake worth reporting.
        """
        current_user: User | None = info.context["current_user"]
        return AccountType.from_model(current_user) if current_user is not None else None

    @strawberry.field(permission_classes=[IsAuthenticated])
    async def my_favorites(
        self, info: Info, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[RecipeType]:
        page_bounds(limit, offset)
        current_user: User = info.context["current_user"]
        if not current_user.favorite_recipe_ids:
            # In([]) would match nothing anyway, but skip the round trip.
            return []

        recipes = await (
            Recipe.find(In(Recipe.id, current_user.favorite_recipe_ids))
            .sort("_id")
            .skip(offset)
            .limit(limit)
            .to_list()
        )
        return [RecipeType.from_model(recipe) for recipe in recipes]
