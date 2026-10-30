import os
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from beanie import PydanticObjectId
from bson.errors import InvalidId
from flask import Request
from werkzeug.security import check_password_hash, generate_password_hash

from app.models.user import User
from app.validation import validate_password

_ALGORITHM = "HS256"
_TOKEN_TTL = timedelta(hours=24)


def hash_password(password: str) -> str:
    validate_password(password)
    return generate_password_hash(password, method="scrypt")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or len(password) > 128:
        return False
    try:
        if password_hash.startswith(("$2a$", "$2b$", "$2y$")):
            # Read old bcrypt hashes without accepting ambiguous truncated passwords.
            if len(password.encode()) > 72:
                return False
            return bcrypt.checkpw(password.encode(), password_hash.encode())
        if not password_hash.startswith("scrypt:"):
            return False
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str) -> str:
    payload: dict[str, object] = {
        "sub": user_id,
        "exp": datetime.now(UTC) + _TOKEN_TTL,
    }
    # Secret is read at call time (not import time) so tests can set it
    # via env without import-order issues.
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=_ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """Returns user_id or None for anything invalid/expired —
    auth failures are a normal outcome, not an exception."""
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None


async def get_current_user(request: Request) -> User | None:
    """The DRF `request.user` equivalent, done by hand:
    Bearer token -> user_id -> User document (or None, never an error).
    """
    header: str = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None

    user_id = decode_access_token(header.removeprefix("Bearer "))
    if user_id is None:
        return None

    try:
        oid = PydanticObjectId(user_id)
    except (InvalidId, ValueError, TypeError):
        return None
    return await User.get(oid)
