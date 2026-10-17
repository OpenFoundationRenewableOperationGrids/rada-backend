# Security Policy

## Reporting a vulnerability

**Please don't report security issues in public issues, discussions or pull requests.**

Report them privately instead: in the **Security** tab of this repository, click **Report a vulnerability**. Only the maintainers can see your report.

Please include:

- what the issue is and what an attacker could do with it;
- the steps to reproduce it, or a proof of concept;
- the affected version or commit.

We will acknowledge your report within a week and keep you informed until it is fixed. Once a fix is released, we are happy to credit you in the advisory if you wish.

## Supported versions

Only the latest version on `main` (the deployed API) receives security fixes.

## Scope

This repository covers the API, its database models, the telemetry simulator and the LLM integration. Issues in the web app belong to [rada-frontend](https://github.com/OpenFoundationRenewableOperationGrids/rada-frontend), but you can report them here too and we will pass them on.
