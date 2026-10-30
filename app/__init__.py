import asyncio
import os
from typing import Any

from flask import Flask, Request, Response, current_app, jsonify, request
from pymongo.errors import PyMongoError
from strawberry.flask.views import AsyncGraphQLView
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from app.auth import get_current_user
from app.db import init_db
from app.rate_limit import MemoryRateLimiter, MongoRateLimiter, RateLimiter, RateLimitExceeded
from app.schema import schema
from app.schema.dataloaders import create_comments_loader, create_dataloaders
from app.views import index


class AuthGraphQLView(AsyncGraphQLView):
    """Puts the authenticated user into the GraphQL context —
    the hand-rolled equivalent of DRF's authentication middleware
    populating request.user. Resolvers read info.context["current_user"].
    """

    # Ignored below: Strawberry's base view annotates get_context as
    # returning None instead of the context type, so any real context
    # implementation is reported as an incompatible override.
    async def get_context(  # type: ignore[override]
        self, request: Request, response: Response
    ) -> dict[str, Any]:
        return {
            "request": request,
            "response": response,
            "current_user": await get_current_user(request),
            # Per-request loader: caching across requests would leak
            # one user's data into another's response.
            "user_loader": create_dataloaders(),
            "comments_loader": create_comments_loader(),
            "rate_limiter": current_app.extensions["rate_limiter"],
        }


def create_app(init_database: bool = True, rate_limiter: RateLimiter | None = None) -> Flask:
    """App factory: the equivalent of what Django does for you via settings + wsgi.py.

    init_database=False lets tests build the app without a live Mongo —
    the same reason Django's test runner swaps DATABASES for you.
    """
    app = Flask(__name__)
    app.config["API_VERSION"] = "1.0.0"
    # Enable only behind the exact number of trusted proxies which sanitize this header.
    proxy_hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))
    if not 0 <= proxy_hops <= 5:
        raise ValueError("TRUSTED_PROXY_HOPS must be between 0 and 5")
    if proxy_hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_hops)  # type: ignore[method-assign]
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.extensions["rate_limiter"] = rate_limiter or (
        MongoRateLimiter() if init_database else MemoryRateLimiter()
    )

    @app.before_request
    def _check_body_size() -> None:
        if request.content_length and request.content_length > app.config["MAX_CONTENT_LENGTH"]:
            raise RequestEntityTooLarge()

    @app.errorhandler(RequestEntityTooLarge)
    def _body_too_large(error: RequestEntityTooLarge) -> tuple[Response, int]:
        return jsonify(error="Request body exceeds 64 KiB"), 413

    if init_database:
        # NOT asyncio.run(init_db()) here: that binds AsyncMongoClient to a
        # temporary loop which is closed straight after, and every later
        # request then fails with "Cannot use AsyncMongoClient in different
        # event loop". Initialising on first request means the client is
        # created on the server's loop — the same one the views run on.
        db_ready = False
        db_lock: asyncio.Lock | None = None

        @app.before_request
        async def _init_db_once() -> None:
            nonlocal db_ready, db_lock
            if db_ready:
                return
            if db_lock is None:
                # Safe without a lock of its own: no await above it,
                # so this branch can't interleave.
                db_lock = asyncio.Lock()
            async with db_lock:
                if not db_ready:
                    await init_db()
                    db_ready = True

    @app.before_request
    async def _limit_requests() -> Response | None:
        limiter = app.extensions["rate_limiter"]
        try:
            # Use the peer address. Never trust arbitrary X-Forwarded-For headers.
            await limiter.consume("http-ip", request.remote_addr or "unknown", 120)
        except RateLimitExceeded as exc:
            response = jsonify(error="Too many requests", code="RATE_LIMITED")
            response.status_code = 429
            response.headers["Retry-After"] = str(exc.retry_after)
            return response
        except PyMongoError:
            response = jsonify(error="Service temporarily unavailable")
            response.status_code = 503
            return response
        return None

    app.add_url_rule("/", view_func=index)

    app.add_url_rule(
        "/graphql",
        view_func=AuthGraphQLView.as_view("graphql_view", schema=schema),
    )

    return app
