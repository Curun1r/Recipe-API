from typing import Annotated

import pydantic
import strawberry
from pymongo.errors import DuplicateKeyError
from strawberry.types import Info

from app.auth import create_access_token, hash_password, verify_password
from app.models.user import User
from app.rate_limit import limit_auth
from app.schema.types.user import AccountType
from app.validation import RegistrationInput, validate_password


@strawberry.type
class AuthPayload:
    token: str
    user: AccountType


@strawberry.type
class AuthError:
    message: str


# Expected failures (duplicate email, bad credentials) are part of the schema,
# not exceptions: the client gets a typed result instead of a 500.
# The GraphQL analogue of DRF's serializer.errors -> 400 response.
AuthResult = Annotated[AuthPayload | AuthError, strawberry.union("AuthResult")]


def _payload(user: User) -> AuthPayload:
    return AuthPayload(
        token=create_access_token(str(user.id)),
        user=AccountType.from_model(user),
    )


@strawberry.type
class AuthMutations:
    @strawberry.mutation
    async def register(self, email: str, password: str, name: str, info: Info) -> AuthResult:
        await limit_auth(info, email)
        try:
            validate_password(password)
            data = RegistrationInput(email=email, name=name)
        except pydantic.ValidationError:
            return AuthError(message="Enter a valid email and a name of 1 to 100 characters")
        except ValueError as exc:
            return AuthError(message=str(exc))
        email, name = str(data.email), data.name
        if await User.find_one(User.email == email) is not None:
            return AuthError(message="Email is already registered")

        try:
            user = User(email=email, password_hash=hash_password(password), name=name)
        except pydantic.ValidationError:
            return AuthError(message="Invalid email address")

        try:
            await user.insert()
        except DuplicateKeyError:
            # Race-safe guard: the unique index catches what find_one missed.
            return AuthError(message="Email is already registered")

        return _payload(user)

    @strawberry.mutation
    async def login(self, email: str, password: str, info: Info) -> AuthResult:
        await limit_auth(info, email)
        if not email or len(email) > 254 or not password or len(password) > 128:
            return AuthError(message="Invalid email or password")
        user = await User.find_one(User.email == email)
        # One message for both cases — don't reveal which emails exist.
        if user is None or not verify_password(password, user.password_hash):
            return AuthError(message="Invalid email or password")
        return _payload(user)
