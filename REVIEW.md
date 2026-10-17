# Review instructions

Rules for automated code reviews on this repository. General project
conventions live in [CONTRIBUTING.md](CONTRIBUTING.md).

## What Important means here

Reserve Important for findings that would break behaviour in production,
leak data, or block a rollback:

- Incorrect logic, unhandled errors that surface as a 500
- A change to a SQLAlchemy model with no migration for the existing
  database (`create_all()` never adds or alters columns)
- A change to a response shape, a route or a status code that is not
  flagged in the pull request description (the frontend depends on them)
- Secrets, keys or database URLs committed, or written to logs
- Timestamps that are not UTC
- A number in an LLM answer computed by the model instead of by SQL or
  Python

Style, naming and refactoring suggestions are Nit at most.

## Cap the nits

Report at most five Nits per review. If you found more, say "plus N
similar items" in the summary instead of posting them inline. If
everything you found is a Nit, lead the summary with "No blocking issues."

## Do not report

- Anything CI already enforces: Ruff lint, `pip check`, failing tests,
  Docker build
- `LICENSE`, and version bumps opened by Dependabot
- Wording and formatting in Markdown files, unless a statement is wrong

## Always check

- A new or changed route has tests for the success case, a business
  error (404, 409…) and a validation error (422)
- The README is updated when a route, a model or an environment variable
  is added or changed
- Specific routes (`/assets/summary`) are declared before parameterised
  ones (`/assets/{asset_id}`)
- Units follow the project conventions: MW, MWh, MVAr
