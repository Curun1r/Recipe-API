# Recipe Aggregator

![CI](https://github.com/Curun1r/Recipe_aggregator/actions/workflows/ci.yml/badge.svg)

A GraphQL API for aggregating recipes — a learning project built to pick up
Flask, GraphQL (Strawberry) and MongoDB (Beanie) as a deliberate contrast to
a Django + DRF + PostgreSQL background. Where a design decision differs from
the Django/DRF equivalent, the code comments say so explicitly.

This is an **API-only** project by choice: the stack above is what's being
demonstrated, so no frontend framework was added. The one HTML page in the
app (`/`) is a plain read-only Flask + Jinja view, not a UI layer.

## Stack

- **Flask** (app factory), served as **ASGI** via `asgiref.wsgi.WsgiToAsgi` + **Hypercorn**
- **Strawberry GraphQL** (code-first schema)
- **MongoDB** via **Beanie** (ODM) on top of **PyMongo Async**
- **JWT** auth (PyJWT + bcrypt), hand-rolled — no framework auth layer
- **pytest** + **httpx** (ASGI transport) for tests, **ruff** + **mypy** for lint/types
- **Docker Compose** for local Mongo + app; **GitHub Actions** CI

## API surface

**Queries**

| Field | Auth | Description |
|---|---|---|
| `recipes` | — | All recipes |
| `recipe(id)` | — | Single recipe, or `null` |
| `me` | — | Current user, or `null` if not logged in |
| `myFavorites` | ✓ | Recipes the current user has favorited |

**Mutations**

| Field | Auth | Returns |
|---|---|---|
| `register(email, password, name)` | — | `AuthPayload \| AuthError` |
| `login(email, password)` | — | `AuthPayload \| AuthError` |
| `createRecipe(input)` | ✓ | `RecipeType` |
| `addComment(recipeId, text)` | ✓ | `CommentPayload \| CommentError` |
| `toggleFavorite(recipeId)` | ✓ | `FavoritePayload \| FavoriteError` |

Auth (✓) is enforced via a `strawberry.permission.BasePermission` class
(`IsAuthenticated`) — a protocol-level GraphQL error, not a field in the
response. Business-level failures ("email already registered", "recipe not
found", "wrong password") are modeled as GraphQL union types instead, so the
client always gets a typed result rather than parsing error strings.

## Running

```bash
docker compose up --build
```

- Recipes page: http://localhost:8000/
- GraphQL playground: http://localhost:8000/graphql

(Port 8000, not 5000 — on macOS the AirPlay Receiver already occupies 5000.)

Populate it with demo data (idempotent — safe to run again):

```bash
docker compose exec app python -m scripts.seed_db
```

Demo accounts (see `scripts/seed_db.py` for the full list):

| Email | Password |
|---|---|
| `ann@example.com` | `demo-pass-ann` |
| `bob@example.com` | `demo-pass-bob` |
| `cleo@example.com` | `demo-pass-cleo` |

### Running locally without Docker

Must be served as ASGI, **not** `flask run`:

```bash
docker compose up -d mongo
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
MONGO_URI=mongodb://localhost:27017/recipe_aggregator \
hypercorn app.wsgi:asgi_app --bind 0.0.0.0:8000
```

`flask run` serves WSGI, which hands every request a fresh event loop.
`AsyncMongoClient` is bound to the loop it was created on, so the second
request would fail with `Cannot use AsyncMongoClient in different event
loop`. Serving through `app/wsgi.py` (`WsgiToAsgi`) keeps everything on one
loop for the life of the process — see the comments there for the full story.

## Example queries

Register and grab a token:

```graphql
mutation {
  register(email: "you@example.com", password: "s3cret!", name: "You") {
    __typename
    ... on AuthPayload { token user { id name } }
    ... on AuthError { message }
  }
}
```

Send it as `Authorization: Bearer <token>`, then:

```graphql
mutation {
  createRecipe(input: {
    title: "Focaccia"
    description: "Airy Italian flatbread."
    ingredients: [{ name: "Bread flour", amount: 500, unit: "g" }]
    steps: [{ order: 1, text: "Knead, rest, bake." }]
    tags: ["bread", "italian"]
  }) {
    id
    title
    author { name }
  }
}
```

Browse recipes with nested comments and authors, resolved through a
per-request `DataLoader` so this stays at one extra query regardless of how
many recipes or comments come back:

```graphql
query {
  recipes {
    title
    author { name }
    comments { text author { name } }
  }
}
```

## Tests and code quality

```bash
pip install -r requirements-dev.txt

ruff check .
ruff format --check .
mypy app tests scripts

docker compose up -d mongo
JWT_SECRET=dev-secret MONGO_URI=mongodb://localhost:27017/recipe_aggregator_test pytest
```

Tests hit a real, disposable Mongo database (`recipe_aggregator_test`,
created and dropped per test module) rather than mocking the driver —
including a couple that go through the actual ASGI stack end-to-end
(`tests/test_views.py`), not just `schema.execute()` in-process.

`requirements.txt` holds runtime dependencies only — what actually ships in
the Docker image. Lint, type-checking and test tooling live in
`requirements-dev.txt`.

## A few deliberate architectural choices

- **Embed vs. reference, chosen per case, not by convention.** A recipe's
  ingredients and steps are embedded Pydantic models (they never exist
  outside their parent). Comments and users are separate collections
  (`Link[User]`), because they're queried and grow independently.
- **DataLoader for N+1, only where GraphQL actually causes it.** The
  read-only `/` page uses `fetch_links=True` (one extra query, eagerly);
  GraphQL resolvers use a per-request `DataLoader` (batched, lazily, only
  for fields the client actually asked for). Using the same tool for both
  would mean either over-fetching on the page or forcing GraphQL machinery
  onto a plain view.
- **Auth vs. business errors are two different mechanisms on purpose.**
  "Not logged in" is a permission class (an exception, like DRF's
  `permission_classes`). "Email already taken" is a typed union in the
  response. Conflating the two would mean either raising exceptions for
  expected outcomes, or leaking access-control into every resolver's return
  type.

## Project structure

```
app/
  __init__.py          # create_app() factory
  auth.py               # password hashing, JWT, get_current_user()
  db.py                 # PyMongo Async client + init_beanie
  wsgi.py                # ASGI entrypoint (WsgiToAsgi) — see "Running" above
  views.py               # read-only Jinja page
  models/                # Beanie documents (User, Recipe, Comment)
  schema/
    types/               # GraphQL types, mirroring the models
    queries/
    mutations/
    dataloaders.py       # per-request DataLoaders
    permissions.py       # IsAuthenticated
scripts/
  seed_db.py             # idempotent demo data
tests/
.github/workflows/ci.yml
```

## Deploy

A Railway deployment is planned (Docker + Hypercorn image, Mongo as a
managed service) but not live yet. Nothing above depends on it — everything
runs the same way locally via `docker compose up --build`.

## License

[MIT](LICENSE)
