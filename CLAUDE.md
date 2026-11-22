# CLAUDE.md — rada-backend

Instructions for Claude Code (and a quick orientation for humans) working in this repository.
Shared by the whole team: keep personal preferences in your own `~/.claude/CLAUDE.md`, not here.

## What this repo is

The backend of **RADA** (Renewable Assets Data Analytics), an open-source platform that consolidates
telemetry from behind-the-meter renewable assets (batteries, PV, wind) across one or more OpenEMS
installations. RADA is the product; OpenFROG (Open Foundation for Renewable Operation Grids) is the
organisation. Product scope and invariants: [SPEC.md](SPEC.md). Stories with acceptance criteria are
GitHub issues `BE-xx` (org Project 1).

## Map

| Path | What |
|---|---|
| `main.py` | FastAPI app: auth, CORS, Pydantic schemas, all routes |
| `models.py` | SQLAlchemy models — must match `alembic/versions/` |
| `database.py` | Engine (psycopg 3), `SessionLocal`, UTC on every connection |
| `alembic/versions/` | Schema migrations `0001`…, numbered with `--rev-id` |
| `llm_service.py` | vLLM client (OpenAI-compatible) for narration |
| `bridge/` | OpenEMS B2B websocket bridge, runs as its own container (`python -m bridge`) |
| `seeds/`, `seed_metrics.py`, `seed_batteries.py` | Metric catalogue and demo data |
| `mvp_sites.py` | Transitional rule giving MVP assets a site — to be removed |
| `tests/`, `bridge/tests/` | pytest suites |
| `docs/DATA_MODEL.md` | V1.0 schema; **the migrations win** where they differ |
| `docs/MIGRATIONS.md` | How to change the schema and upgrade preprod/production |
| `REVIEW.md` | Rules for the automated `@claude review` |

## Commands

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn main:app --reload                 # Swagger at /docs when ENVIRONMENT=development
ruff check .                              # lint (CI)
python -m pytest -v                       # all tests; needs a rada_test DB on the DATABASE_URL server
python -m pytest bridge -v                # bridge only
alembic upgrade head && alembic check     # models and migrations agree
alembic revision --autogenerate --rev-id 00NN -m "short description"
docker build -t rada-backend .
```

Run `ruff check .` and `python -m pytest` before saying a change is done.

## Rules that must not be broken

1. **LLMs narrate, Python computes.** Every number in an LLM answer comes from SQL or Python. Never
   let the model add, average or convert values.
2. **UTC everywhere.** Store, accept and return UTC (`timestamptz`). Local time is the frontend's job.
3. **Units.** New tables store W, Wh, var and %, with OpenEMS signs (grid + = import, battery + =
   discharge). MVP columns `power_mw`, `energy_mwh`, `reactive_power_mvar` stay in MW/MWh/MVAr
   until the front end has switched. See `docs/DATA_MODEL.md` §3.
4. **Schema changes go through Alembic.** A model change and its revision land in the same PR.
   Never edit a revision already merged to `develop`; add a new one. Never use `create_all()`.
5. **The API contract is shared.** The web app and the mobile apps depend on response shapes, routes
   and status codes. If you change one, say so in the PR description and open an issue in
   `rada-frontend` (and `rada-mobile` if affected).
6. **Secrets come from the environment.** Never commit `.env*` (except `.env.example`), keys,
   passwords, database URLs or `bridge/connections.toml`. Never print secrets in logs or output.
7. **Tests never touch real data.** `tests/conftest.py` refuses any database not ending in `_test`.
   Do not weaken that guard. Never run seeds with `--reset`, migrations or `DROP` against preprod or
   production from a local session.
8. **Routes:** declare specific routes (`/assets/summary`) before parameterised ones (`/assets/{id}`).
9. **Demo data** is commercial/community scale (MW, MWh, a few behind-the-meter wind sites), never
   domestic-sized.

## How changes are made

- Branch from `develop`: `feat/be-NN-short-name`, `fix/…`, `docs/…`, `test/…`, `chore/…`.
  Nothing targets `main` until the V1.0 release PR.
- Conventional Commits; the PR title is checked and becomes the squash commit.
- One story can be several PRs. Aim for PRs a reviewer can read in one sitting (roughly under
  400 changed lines of hand-written code; generated migrations and lock files don't count).
- A new or changed route has tests for success, a business error (404/409) and a validation error (422).
  A bug fix has a test that fails without the fix.
- Update the README when a route, model or environment variable changes. New docs follow the
  English-then-French pattern of `docs/DATA_MODEL.md` and `docs/MIGRATIONS.md`.
- Flow: local → PR (CI green, `@claude review`, one human approval) → preprod verification → production.

## What Claude should and should not do here

- Read the relevant issue (`gh issue view NN`) and `SPEC.md` before proposing a design.
- Prefer small, explained diffs over rewriting whole files. Show the change and why; the developer
  applies or approves it.
- Ask before: pushing, opening/closing/editing issues or PRs, changing labels or assignees, moving a
  story's scope or owner, adding a dependency, or adding a new container.
- If something in the code contradicts SPEC.md, an issue or a decision recorded in the docs, say so
  instead of working around it.
- Do not run anything against the VPS, preprod or production. Deployment steps are in
  `docs/MIGRATIONS.md` and are carried out by a person.
- Documentation is English first, then French (`*[Français ci-dessous]*` pattern). Mermaid diagrams
  use `flowchart`, `<br>` in labels, and black node text (`classDef default color:#000`).
