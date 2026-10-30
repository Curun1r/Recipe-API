from flask import abort, render_template, request

from app.models.recipe import Recipe
from app.validation import DEFAULT_PAGE_SIZE, MAX_OFFSET


async def index() -> str:
    """Read-only demo page.

    No DataLoader and no GraphQL layer here on purpose: this page always
    needs every author, so fetch_links=True ($lookup) resolves them in one
    round trip. DataLoaders exist to batch what GraphQL asks for *lazily* —
    pulling them in here would be cargo-culting.
    """
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        abort(400, description="page must be a positive integer")
    if not 1 <= page <= MAX_OFFSET // DEFAULT_PAGE_SIZE + 1:
        abort(400, description="page is out of range")
    offset = (page - 1) * DEFAULT_PAGE_SIZE
    recipes = await (
        Recipe.find_all(fetch_links=True)
        .sort("_id")
        .skip(offset)
        .limit(DEFAULT_PAGE_SIZE + 1)
        .to_list()
    )
    return render_template(
        "index.html",
        recipes=recipes[:DEFAULT_PAGE_SIZE],
        page=page,
        has_next=len(recipes) > DEFAULT_PAGE_SIZE and offset < MAX_OFFSET,
    )
