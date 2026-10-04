# Contributing to RADA Backend

Thanks for your interest in RADA! Bug reports, ideas and pull requests are all welcome.

Everyone taking part in the project follows the [OpenFROG Code of Conduct](https://github.com/OpenFoundationRenewableOperationGrids/.github/blob/main/CODE_OF_CONDUCT.md).

## Before you start

- **Bugs and ideas:** open an [issue](../../issues/new/choose) first, so we can agree on the approach before you write code.
- **Security issues:** don't open a public issue. Follow [SECURITY.md](SECURITY.md).
- **Frontend changes:** the web app lives in [rada-frontend](https://github.com/OpenFoundationRenewableOperationGrids/rada-frontend).

## Set up

You need Python 3.11 (the version of the `Dockerfile`) and PostgreSQL with TimescaleDB.

```bash
git clone https://github.com/OpenFoundationRenewableOperationGrids/rada-backend.git
cd rada-backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: see the README
pip install -r requirements.txt
cp .env.example .env               # then fill in DATABASE_URL and VLLM_BASE_URL
uvicorn main:app --reload
```

With `AUTH_ENABLED=false` and `ENVIRONMENT=development`, Swagger is at http://127.0.0.1:8000/docs and no key is needed.

## Branches

- `main` is production, deployed on the VPS.
- `develop` is the integration branch. **Open every pull request against `develop`.**
- Name your branch after the change: `feat/…`, `fix/…`, `docs/…`, `test/…`, `chore/…`.

## Commits

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(assets): add PATCH /assets/{asset_id} for partial updates
fix(llm): return 503 when the vLLM server is unreachable
```

Keep each commit focused on one change.

## Checks

CI runs these on every pull request, and all of them must pass:

```bash
pip check                 # no conflicting dependencies
python -m pytest -v       # API tests, against a real PostgreSQL database
docker build -t rada-backend .
```

## Tests

The tests use pytest and FastAPI's `TestClient`, against a dedicated `rada_test` database on the same server as `DATABASE_URL`. They never touch your development data. Create it once:

```bash
psql -U <user> -h localhost -c "CREATE DATABASE rada_test;"
```

[TESTING.md](TESTING.md) explains the fixtures and how to write a test for a new route.

New features and bug fixes should come with tests. A bug fix should include a test that fails without the fix. For a route, cover at least the success case, a business error (404, 409…) and a validation error (422).

## Code guidelines

- **Units:** MW for power, MWh for energy, MVAr for reactive power. The sign of `power_mw` gives the charge/discharge direction.
- **UTC everywhere:** the API stores, receives and returns UTC timestamps. Conversion to local time is the frontend's job.
- **Routes:** declare specific routes (`/assets/summary`) before parameterised ones (`/assets/{asset_id}`).
- **LLM:** the model narrates, Python computes. Any number in an answer must come from a SQL or Python function, never from the model's own arithmetic.
- **API contract:** the frontend depends on the response shapes. If you change one, say so in the pull request and open a matching issue in rada-frontend.
- **Secrets:** read them from environment variables. Never commit `.env` files, keys or database URLs.

## Pull requests

- Describe what changes and why, and how you tested it.
- Update the README when you add or change a route, a model or an environment variable.
- Keep pull requests small. Several small ones are easier to review than a large one.

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).
