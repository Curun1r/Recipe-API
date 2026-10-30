import strawberry

from app.models.user import User


@strawberry.type
class UserType:
    """Public author identity. Account email is never part of this type."""

    id: strawberry.ID
    name: str

    @classmethod
    def from_model(cls, user: User) -> "UserType":
        return cls(id=strawberry.ID(str(user.id)), name=user.name)


@strawberry.type
class AccountType(UserType):
    """Private account data, returned only by login, registration and me."""

    email: str

    @classmethod
    def from_model(cls, user: User) -> "AccountType":
        return cls(id=strawberry.ID(str(user.id)), email=user.email, name=user.name)
