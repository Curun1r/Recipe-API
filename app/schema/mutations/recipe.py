import strawberry
from graphql import GraphQLError
from pydantic import ValidationError
from strawberry.types import Info

from app.models.recipe import Ingredient, Recipe, Step
from app.models.user import User
from app.schema.permissions import IsAuthenticated
from app.schema.types.recipe import RecipeType
from app.validation import RecipeData


@strawberry.input
class IngredientInput:
    name: str
    amount: float
    unit: str


@strawberry.input
class StepInput:
    order: int
    text: str


@strawberry.input
class CreateRecipeInput:
    title: str
    description: str
    ingredients: list[IngredientInput]
    steps: list[StepInput]
    tags: list[str] = strawberry.field(default_factory=list)


@strawberry.type
class RecipeMutations:
    @strawberry.mutation(permission_classes=[IsAuthenticated])
    async def create_recipe(self, input: CreateRecipeInput, info: Info) -> RecipeType:
        # Author comes from the token, never from input — otherwise any
        # client could publish recipes as somebody else.
        current_user: User = info.context["current_user"]

        try:
            data = RecipeData.model_validate(strawberry.asdict(input))
        except ValidationError as exc:
            raise GraphQLError(
                "Invalid recipe input",
                extensions={
                    "code": "BAD_USER_INPUT",
                    "fields": [
                        {"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                        for e in exc.errors(include_input=False, include_url=False)
                    ],
                },
            ) from None

        recipe = Recipe(
            title=data.title,
            description=data.description,
            ingredients=[
                Ingredient(name=i.name, amount=i.amount, unit=i.unit) for i in data.ingredients
            ],
            steps=[Step(order=s.order, text=s.text) for s in data.steps],
            tags=list(data.tags),
            author=current_user,
        )
        await recipe.insert()

        return RecipeType.from_model(recipe)
