"""
auth

Sign-in, roles and API keys for RADA (BE-04, docs/AUTH.md). Local email and
password accounts in 1.0; built so that an identity server (rada-auth, OIDC)
can replace the session-token part later without touching the endpoints.

- config:   settings read from the environment
- security: password hashing, password rules, session tokens, API keys
- throttle: slowing down repeated failed sign-ins
- deps:     who is calling (get_principal) and the require_* dependencies
- routes:   /auth, /users and /api-keys
"""
