import hashlib
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol

from graphql import GraphQLError
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from strawberry.types import Info

from app.models.user import User


@dataclass
class RateLimitExceeded(Exception):
    retry_after: int


class RateLimiter(Protocol):
    async def consume(self, scope: str, key: str, limit: int, window: int = 60) -> None: ...


def bucket_key(scope: str, key: str, window: int) -> tuple[str, int, float]:
    now = time.time()
    end = (int(now // window) + 1) * window
    digest = hashlib.sha256(f"{scope}:{key}:{end}".encode()).hexdigest()
    return digest, max(1, math.ceil(end - now)), end


class MongoRateLimiter:
    """Atomic fixed-window counters shared by all app processes; no memory fallback."""

    async def consume(self, scope: str, key: str, limit: int, window: int = 60) -> None:
        digest, retry_after, end = bucket_key(scope, key, window)
        collection = User.get_pymongo_collection().database["api_rate_limits"]
        update: dict[str, Any] = {
            "$inc": {"count": 1},
            "$setOnInsert": {"expires_at": datetime.fromtimestamp(end + window, UTC)},
        }
        try:
            counter = await collection.find_one_and_update(
                {"_id": digest}, update, upsert=True, return_document=ReturnDocument.AFTER
            )
        except DuplicateKeyError:
            # Another process inserted this bucket between our lookup and upsert.
            counter = await collection.find_one_and_update(
                {"_id": digest}, {"$inc": {"count": 1}}, return_document=ReturnDocument.AFTER
            )
        if counter is None:
            raise RuntimeError("Rate limit counter could not be updated")
        if counter["count"] > limit:
            raise RateLimitExceeded(retry_after)


class MemoryRateLimiter:
    """Isolated test storage, selected only when database initialization is disabled."""

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[int, float]] = {}
        self._lock = Lock()

    async def consume(self, scope: str, key: str, limit: int, window: int = 60) -> None:
        digest, retry_after, end = bucket_key(scope, key, window)
        with self._lock:
            self._buckets = {k: v for k, v in self._buckets.items() if v[1] > time.time()}
            count = self._buckets.get(digest, (0, end))[0] + 1
            self._buckets[digest] = (count, end)
        if count > limit:
            raise RateLimitExceeded(retry_after)


async def limit_auth(info: Info, email: str) -> None:
    # The request context is constructed by the server, never supplied by the client.
    context = info.context or {}
    limiter = context.get("rate_limiter")
    request = context.get("request")
    if limiter is None or request is None:
        return
    ip = request.remote_addr or "unknown"
    try:
        await limiter.consume("auth-ip", ip, 10)
        await limiter.consume("auth-account", email.strip().casefold(), 20)
        await limiter.consume("auth-ip-account", f"{ip}:{email.strip().casefold()}", 5)
    except RateLimitExceeded as exc:
        raise GraphQLError(
            "Too many authentication attempts. Try again later.",
            extensions={"code": "RATE_LIMITED", "retryAfter": exc.retry_after},
        ) from None
    except PyMongoError:
        raise GraphQLError(
            "Authentication is temporarily unavailable.",
            extensions={"code": "SERVICE_UNAVAILABLE"},
        ) from None
