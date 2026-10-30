from typing import Annotated

from graphql import GraphQLError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20
MAX_OFFSET = 10_000
MAX_COMMENT_PAGE_SIZE = 20
DEFAULT_COMMENT_PAGE_SIZE = 10


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)


class RegistrationInput(InputModel):
    email: EmailStr = Field(max_length=254)
    name: str = Field(min_length=1, max_length=100)


def validate_password(password: str) -> None:
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Password must contain valid Unicode characters") from None
    if not 12 <= len(password) <= 128 or not password.strip():
        raise ValueError("Password must contain 12 to 128 characters and cannot be blank")


class IngredientData(InputModel):
    name: str = Field(min_length=1, max_length=100)
    amount: float = Field(gt=0, le=1_000_000_000)
    unit: str = Field(min_length=1, max_length=30)


class StepData(InputModel):
    order: int = Field(ge=1, le=50)
    text: str = Field(min_length=1, max_length=2000)


Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class RecipeData(InputModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    ingredients: list[IngredientData] = Field(min_length=1, max_length=50)
    steps: list[StepData] = Field(min_length=1, max_length=50)
    tags: list[Tag] = Field(default_factory=list, max_length=20)

    @field_validator("steps")
    @classmethod
    def ordered_steps(cls, steps: list[StepData]) -> list[StepData]:
        if [step.order for step in steps] != list(range(1, len(steps) + 1)):
            raise ValueError("Steps must be ordered consecutively starting at 1")
        return steps

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, tags: list[str]) -> list[str]:
        if len({tag.casefold() for tag in tags}) != len(tags):
            raise ValueError("Tags must be unique")
        return tags


def page_bounds(limit: int, offset: int, maximum: int = MAX_PAGE_SIZE) -> tuple[int, int]:
    if not 1 <= limit <= maximum or not 0 <= offset <= MAX_OFFSET:
        raise GraphQLError(
            f"limit must be between 1 and {maximum}; offset must be between 0 and {MAX_OFFSET}",
            extensions={"code": "BAD_USER_INPUT"},
        )
    return limit, offset
