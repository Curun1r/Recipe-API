import os

from beanie import init_beanie
from pymongo import AsyncMongoClient

from app.models.comment import Comment
from app.models.recipe import Recipe
from app.models.user import User


async def init_db(mongo_uri: str | None = None) -> None:
    """Initialize the MongoDB connection.

    Roughly Django's DATABASES setting and migrations combined:
    Beanie has no migrations — init_beanie registers document models
    and creates indexes at startup.
    """
    uri: str = mongo_uri or os.environ["MONGO_URI"]
    # tz_aware=True: BSON always stores UTC, but decodes to *naive* datetimes
    # by default — so a tz-aware created_at would come back naive and
    # comparisons would blow up. Django's USE_TZ, done by hand.
    client: AsyncMongoClient = AsyncMongoClient(uri, tz_aware=True)

    await init_beanie(
        database=client.get_default_database(),
        document_models=[Recipe, User, Comment],
    )

    await client.get_default_database()["api_rate_limits"].create_index(
        "expires_at", expireAfterSeconds=0
    )
