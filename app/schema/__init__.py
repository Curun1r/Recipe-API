import strawberry
from strawberry.extensions import MaxAliasesLimiter, MaxTokensLimiter, QueryDepthLimiter

from app.schema.mutations import (
    AuthMutations,
    CommentMutations,
    FavoriteMutations,
    RecipeMutations,
)
from app.schema.queries import RecipeQueries, UserQueries


@strawberry.type
class Query(RecipeQueries, UserQueries):
    @strawberry.field
    def hello(self) -> str:
        return "world"


@strawberry.type
class Mutation(AuthMutations, RecipeMutations, CommentMutations, FavoriteMutations):
    """Root mutation, composed from per-domain mutation classes."""


schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    extensions=[
        lambda: MaxAliasesLimiter(max_alias_count=10),
        lambda: MaxTokensLimiter(max_token_count=2000),
        lambda: QueryDepthLimiter(max_depth=12),
    ],
)
