# Recipe API — deployment readiness review

Reviewed on 30 October 2026 against local source based on commit `486c62c`.

## Assessment

The existing scope is sufficient for a backend portfolio project: registration/login, recipe creation and queries, comments, favorites, per-request DataLoaders, typed business errors, integration tests and CI. A public demo needs a focused hardening pass before deployment. A larger feature set or a full frontend is not required to demonstrate this API.

This change updates the display name in the README and the HTML title/heading to **Recipe API**. The GitHub URL and MongoDB database identifiers retain their existing names to preserve links and data compatibility. The findings below are documented, not implemented by this naming change.

## Fix before an unrestricted public demo

### 1. Keep account email out of public author results

`app/schema/types/user.py:7` exposes `email` on the same `UserType` returned by public recipe/comment author resolvers. An unauthenticated HTTP request to `{ recipes { author { email } } }` successfully returned the synthetic test author's email.

Use a public author type containing only public identity fields, and expose the account email through the authenticated current-user/authentication response. Add a regression test proving guests cannot obtain an author's account email.

### 2. Validate passwords without silently truncating them

`app/auth.py:21` and `app/auth.py:25` slice encoded passwords to 72 bytes. Two different passwords sharing those first 72 bytes both verify against the same hash. Registration also accepts an empty password and empty name (`app/schema/mutations/auth.py:39`). Both behaviors were reproduced; blank registration was also confirmed through HTTP.

Define and enforce a password policy before hashing. Reject unsupported lengths explicitly instead of truncating. If changing the hashing scheme, plan compatibility with existing hashes; do not invalidate existing accounts accidentally. Test empty, long and multibyte passwords, plus successful registration/login.

### 3. Bound public reads and expensive requests

`app/schema/queries/recipe.py:15`, `app/views.py:14` and the comments loader return complete matching collections. `myFavorites` is also unpaginated. `app/schema/__init__.py:24` configures no application-specific query token/alias limits, and `create_app()` leaves Flask's request body limit unset. Login and registration have no application rate limiter.

Add bounded pagination for recipes/comments/favorites, maximum request/input sizes, GraphQL document/alias limits and limits on authentication attempts. DataLoader reduces repeated queries but does not bound the total amount of data returned. Depth alone is not the main issue for this relatively shallow schema.

Reference: [GraphQL security guidance](https://graphql.org/learn/security/).

### 4. Reject invalid recipe and comment content

`app/schema/mutations/recipe.py:24` and the document models lack business constraints for blank text, list sizes, quantities and preparation-step ordering. An authenticated HTTP mutation accepted a blank recipe title/description, an ingredient with a negative quantity and an empty steps list. `add_comment` also has no non-empty/maximum-length check.

Validate trimmed text, bounded list lengths, finite positive quantities and valid preparation steps. Return predictable validation results and cover invalid input in tests. GraphQL scalar types alone do not enforce these rules.

### 5. Finish the deployment configuration

- `Dockerfile:8` copies the entire build context, and the repository has no `.dockerignore`. Exclude local secrets, virtual environments, Git metadata and caches before building/uploading an image.
- `Dockerfile:12` binds to a fixed port 5000. Configure the hosting service to target that port, or support its injected `PORT` value.
- `.env.example` contains only `MONGO_URI`, while token signing requires `JWT_SECRET`. Document every required variable, use a strong deployment-specific secret and fail early when configuration is missing. Keep the local Compose fallback out of production.
- Add a small readiness endpoint that checks database connectivity and configure the host to use it. The root page currently reads recipes and is not a dedicated health check.
- Use authenticated/private MongoDB connectivity, persistent storage and HTTPS on the hosting platform. Do not seed a deployed database with the public demo-account passwords from `scripts/seed_db.py`.

If using the planned Railway deployment, see its [port and health check contract](https://docs.railway.com/deployments/healthchecks).

## Useful before the first release

- Lock tested dependency versions. Runtime requirements are mostly unconstrained; Compose uses `mongo:latest`, while CI declares MongoDB 7. Align the supported MongoDB version across development, CI and hosting.
- Retain a test that exercises the real first-request database initialization. Existing HTTP fixtures initialize Beanie themselves and pass `init_database=False`; the additional review probe verified the actual cold-start path separately.
- Add regression tests for the issues above. The existing passing suite does not cover these boundaries.
- Clarify the `toggleFavorite` concurrency claim: atomic `$addToSet`/`$pull` prevents lost array updates and duplicate ids, but deciding the action from a previously read user object is not a fully atomic toggle. Explicit set/unset semantics would be easier to retry predictably.

## Optional product improvements

Recipe filtering/search, sorting, and owner-only edit/delete operations would make the API more useful. They can follow the first demo; they are not necessary to present the current learning goals. The existing read-only page and GraphiQL are sufficient for a backend-focused showcase.

## Verification performed

- Python 3.12.3; existing virtual environment and installed dependencies.
- Ruff lint and formatting checks pass: 36 Python files.
- mypy passes: 34 source files.
- Full existing suite: 25 tests pass using an isolated temporary MongoDB 8.2.12 instance. This does not establish the current remote CI result or verify CI's MongoDB 7 environment.
- Additional HTTP requests through the real ASGI adapter confirmed successful cold-start initialization and Recipe API branding, and reproduced blank registration, invalid recipe acceptance and public author-email exposure using synthetic data only.
- Password helper checks reproduced the 72-byte collision behavior.
- No production deployment, load test, dependency vulnerability audit or full container image build was performed.
