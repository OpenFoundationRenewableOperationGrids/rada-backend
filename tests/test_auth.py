"""
test_auth.py

BE-04: first administrator, sign-in by cookie (web) and by token (native
apps), sign-out, "who am I", password rules, slowing down failed sign-ins,
admin/viewer roles, named API keys with scopes and revocation, and the MVP's
single API_KEY still accepted until BE-09.
"""

import pytest
from sqlalchemy import select

import models
from auth import config, security, throttle
from auth.throttle import LoginThrottle

ADMIN_EMAIL = "admin@example.org"
ADMIN_PASSWORD = "correct horse battery staple"
VIEWER_EMAIL = "viewer@example.org"
VIEWER_PASSWORD = "a long viewer passphrase"

ASSET = {
    "eic_code": "17W-AUTH-0001---X", "name": "Auth test battery", "asset_type": "battery",
    "max_capacity_mwh": 4.0, "max_charge_rate_mw": 1.9, "max_discharge_rate_mw": 1.9,
}
TELEMETRY = {"timestamp": "2026-01-01T12:00:00Z", "energy_mwh": 2.0, "power_mw": 0.5}


# --- Helpers ---

def setup_admin(client):
    response = client.post("/auth/setup", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 201, response.text
    return response


def bearer(client, email, password):
    response = client.post("/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def admin(client):
    """Headers of a signed-in administrator (the first one, created by setup)."""
    setup_admin(client)
    client.cookies.clear()
    return bearer(client, ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture()
def viewer(client, admin):
    response = client.post("/users", headers=admin, json={"email": VIEWER_EMAIL, "password": VIEWER_PASSWORD})
    assert response.status_code == 201, response.text
    return bearer(client, VIEWER_EMAIL, VIEWER_PASSWORD)


def make_key(client, admin, name, scope):
    response = client.post("/api-keys", headers=admin, json={"name": name, "scope": scope})
    assert response.status_code == 201, response.text
    return response.json()


def create_asset(client, headers):
    response = client.post("/assets", headers=headers, json=ASSET)
    assert response.status_code == 201, response.text
    return response.json()["asset_id"]


# --- Installation and first administrator ---

def test_new_installation_needs_setup(client):
    assert client.get("/auth/setup-status").json() == {"needs_setup": True, "setup_token_required": False}


def test_setup_creates_the_first_admin_and_signs_in(client, db_session):
    response = setup_admin(client)

    body = response.json()
    assert body["user"]["email"] == ADMIN_EMAIL
    assert body["user"]["role"] == "admin"
    assert client.get("/auth/setup-status").json()["needs_setup"] is False
    assert client.get("/auth/me").json()["email"] == ADMIN_EMAIL  # the cookie is set
    stored = db_session.scalars(select(models.AppUser)).one()
    assert stored.password_hash.startswith("$argon2id$")
    assert ADMIN_PASSWORD not in stored.password_hash


def test_setup_works_only_once(client):
    setup_admin(client)
    response = client.post("/auth/setup", json={"email": "second@example.org", "password": ADMIN_PASSWORD})
    assert response.status_code == 409


def test_setup_applies_the_password_rules(client):
    response = client.post("/auth/setup", json={"email": ADMIN_EMAIL, "password": "Short1!"})
    assert response.status_code == 422
    assert client.get("/auth/setup-status").json()["needs_setup"] is True


def test_setup_rejects_an_invalid_email(client):
    response = client.post("/auth/setup", json={"email": "not-an-email", "password": ADMIN_PASSWORD})
    assert response.status_code == 422


def test_setup_token_is_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(config.settings, "setup_token", "install-secret")
    body = {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}

    assert client.get("/auth/setup-status").json()["setup_token_required"] is True
    assert client.post("/auth/setup", json=body).status_code == 403
    assert client.post("/auth/setup", json=body, headers={"X-Setup-Token": "wrong"}).status_code == 403
    assert client.post("/auth/setup", json=body, headers={"X-Setup-Token": "install-secret"}).status_code == 201


# --- Sign-in, sign-out, who am I ---

def test_login_sets_a_secure_httponly_cookie(client):
    setup_admin(client)
    client.cookies.clear()

    response = client.post("/auth/login", json={"email": "Admin@Example.org ", "password": ADMIN_PASSWORD})

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{config.SESSION_COOKIE}=")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert attribute.lower() in cookie.lower()
    assert "access_token" not in response.json()  # the web never sees the token
    assert client.get("/auth/me").json()["role"] == "admin"


def test_token_login_returns_the_token_without_a_cookie(client):
    setup_admin(client)
    client.cookies.clear()

    response = client.post("/auth/token", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    body = response.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 8 * 3600
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == ADMIN_EMAIL


def test_wrong_password_and_unknown_email_give_the_same_answer(client):
    setup_admin(client)
    wrong_password = client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": "not the password at all"})
    unknown_email = client.post("/auth/login", json={"email": "nobody@example.org", "password": ADMIN_PASSWORD})

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json() == {"detail": "Invalid email or password"}


def test_me_without_a_session_is_401(client):
    assert client.get("/auth/me").status_code == 401


def test_me_with_an_api_key_is_403(client, auth_headers):
    assert client.get("/auth/me", headers=auth_headers).status_code == 403


def test_tampered_token_is_rejected(client, admin):
    token = admin["Authorization"] + "x"
    assert client.get("/auth/me", headers={"Authorization": token}).status_code == 401


def test_logout_ends_the_session(client, admin):
    assert client.post("/auth/logout", headers=admin).status_code == 204
    assert client.get("/auth/me", headers=admin).status_code == 401


def test_logout_clears_the_cookie(client):
    setup_admin(client)
    response = client.post("/auth/logout")
    assert response.status_code == 204
    assert 'rada_session=""' in response.headers["set-cookie"] or "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/auth/me").status_code == 401


def test_logout_without_a_session_is_still_204(client):
    assert client.post("/auth/logout").status_code == 204


def test_signing_in_again_after_logout_works(client, admin):
    client.post("/auth/logout", headers=admin)
    again = bearer(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert client.get("/auth/me", headers=again).status_code == 200


def test_cookie_write_from_another_origin_is_refused(client):
    setup_admin(client)  # signed in by cookie
    evil = client.post("/assets", json=ASSET, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    allowed = client.post("/assets", json=ASSET, headers={"Origin": "http://localhost:3000"})
    assert allowed.status_code == 201


# --- Password change ---

def test_password_change_ends_sessions_and_the_new_password_works(client, admin):
    new_password = "an entirely new passphrase"
    response = client.post("/auth/password", headers=admin,
                           json={"current_password": ADMIN_PASSWORD, "new_password": new_password})

    assert response.status_code == 204
    assert client.get("/auth/me", headers=admin).status_code == 401
    assert client.post("/auth/token", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).status_code == 401
    bearer(client, ADMIN_EMAIL, new_password)


def test_password_change_needs_the_current_password(client, admin):
    response = client.post("/auth/password", headers=admin,
                           json={"current_password": "not my password", "new_password": "an entirely new passphrase"})
    assert response.status_code == 401


# --- Password rules ---

@pytest.mark.parametrize("password", ["fourteen chars", "a" * 20, "x" * 257, "admin@example.org is me!"])
def test_password_rules_refuse(password):
    assert security.password_problem(password, ADMIN_EMAIL) is not None


@pytest.mark.parametrize("password", ["fifteen chars!!", "nocomposition rules needed here", "é" * 3 + "ünïcødé passphrase"])
def test_password_rules_accept_long_passphrases_without_composition_rules(password):
    assert security.password_problem(password, ADMIN_EMAIL) is None


# --- Slowing down failed sign-ins ---

def test_repeated_failures_are_slowed_down(client):
    setup_admin(client)
    client.cookies.clear()
    wrong = {"email": ADMIN_EMAIL, "password": "definitely not the password"}

    for _ in range(throttle.by_email.free_failures):
        assert client.post("/auth/login", json=wrong).status_code == 401
    client.post("/auth/login", json=wrong)  # one more failure: the account must now wait

    blocked = client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


def test_throttle_delay_doubles_and_is_forgotten():
    now = [1000.0]
    limiter = LoginThrottle(free_failures=2, base_delay=1, max_delay=8, forget_after=60, clock=lambda: now[0])
    for _ in range(2):
        limiter.record_failure("k")
    assert limiter.retry_after("k") == 0

    delays = []
    for _ in range(5):
        limiter.record_failure("k")
        delays.append(limiter.retry_after("k"))
    assert delays == [1, 2, 4, 8, 8]

    now[0] += 61
    assert limiter.retry_after("k") == 0


def test_success_resets_the_account_counter():
    limiter = LoginThrottle(free_failures=1)
    limiter.record_failure("k")
    limiter.record_failure("k")
    limiter.record_success("k")
    assert limiter.retry_after("k") == 0


# --- Roles ---

def test_viewer_reads_but_cannot_write(client, viewer):
    assert client.get("/assetslist", headers=viewer).status_code == 200
    assert client.get("/assets/summary", headers=viewer).status_code == 200

    response = client.post("/assets", headers=viewer, json=ASSET)
    assert response.status_code == 403
    assert response.json() == {"detail": "Administrator role required"}


def test_viewer_cannot_manage_users_or_keys(client, viewer):
    assert client.get("/users", headers=viewer).status_code == 403
    assert client.post("/api-keys", headers=viewer, json={"name": "x", "scope": "admin"}).status_code == 403


def test_admin_writes(client, admin):
    create_asset(client, admin)


def test_no_credentials_is_401(client):
    response = client.get("/assetslist")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


# --- Users ---

def test_create_user_is_viewer_by_default_and_email_is_unique(client, admin):
    created = client.post("/users", headers=admin, json={"email": "New@Example.org", "password": VIEWER_PASSWORD})
    assert created.status_code == 201
    assert created.json()["role"] == "viewer" and created.json()["email"] == "new@example.org"

    again = client.post("/users", headers=admin, json={"email": "new@example.org", "password": VIEWER_PASSWORD})
    assert again.status_code == 409


def test_create_user_applies_the_password_rules(client, admin):
    response = client.post("/users", headers=admin, json={"email": "new@example.org", "password": "too short"})
    assert response.status_code == 422


def test_promoted_viewer_becomes_admin_without_signing_in_again(client, admin, viewer):
    user_id = client.get("/auth/me", headers=viewer).json()["id"]
    assert client.patch(f"/users/{user_id}", headers=admin, json={"role": "admin"}).status_code == 200
    create_asset(client, viewer)


def test_deactivated_user_loses_access_and_cannot_sign_in(client, admin, viewer):
    user_id = client.get("/auth/me", headers=viewer).json()["id"]
    assert client.patch(f"/users/{user_id}", headers=admin, json={"is_active": False}).status_code == 200

    assert client.get("/assetslist", headers=viewer).status_code == 401
    response = client.post("/auth/token", json={"email": VIEWER_EMAIL, "password": VIEWER_PASSWORD})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_admin_resets_a_password(client, admin, viewer):
    user_id = client.get("/auth/me", headers=viewer).json()["id"]
    response = client.patch(f"/users/{user_id}", headers=admin, json={"password": "reset by the administrator"})
    assert response.status_code == 200
    assert client.get("/auth/me", headers=viewer).status_code == 401
    bearer(client, VIEWER_EMAIL, "reset by the administrator")


def test_last_active_admin_cannot_be_demoted_or_deactivated(client, admin):
    admin_id = client.get("/auth/me", headers=admin).json()["id"]
    assert client.patch(f"/users/{admin_id}", headers=admin, json={"role": "viewer"}).status_code == 409
    assert client.patch(f"/users/{admin_id}", headers=admin, json={"is_active": False}).status_code == 409


def test_patch_unknown_user_is_404(client, admin):
    assert client.patch("/users/999999", headers=admin, json={"role": "viewer"}).status_code == 404


def test_patch_user_refuses_null(client, admin):
    admin_id = client.get("/auth/me", headers=admin).json()["id"]
    assert client.patch(f"/users/{admin_id}", headers=admin, json={"role": None}).status_code == 422


# --- API keys ---

def test_api_key_is_shown_once_and_stored_hashed(client, admin, db_session):
    created = make_key(client, admin, "bridge-openems", "ingest")

    key = created["key"]
    assert key.startswith("rada_") and created["prefix"] == key[:12]
    stored = db_session.scalars(select(models.ApiKey)).one()
    assert stored.key_hash == security.hash_api_key(key) and key not in stored.key_hash
    listed = client.get("/api-keys", headers=admin).json()
    assert len(listed) == 1 and "key" not in listed[0] and "key_hash" not in listed[0]


def test_api_key_names_are_unique(client, admin):
    make_key(client, admin, "bridge-openems", "ingest")
    response = client.post("/api-keys", headers=admin, json={"name": "bridge-openems", "scope": "export"})
    assert response.status_code == 409


def test_api_key_rejects_an_unknown_scope(client, admin):
    response = client.post("/api-keys", headers=admin, json={"name": "x", "scope": "everything"})
    assert response.status_code == 422


def test_ingest_key_posts_telemetry_but_cannot_read_or_configure(client, admin):
    asset_id = create_asset(client, admin)
    ingest = {"X-API-Key": make_key(client, admin, "bridge-openems", "ingest")["key"]}

    assert client.post(f"/assets/{asset_id}/telemetry", headers=ingest, json=TELEMETRY).status_code == 201
    assert client.get("/assetslist", headers=ingest).status_code == 403
    assert client.post("/assets", headers=ingest, json={**ASSET, "eic_code": "17W-AUTH-0002---X"}).status_code == 403


def test_export_key_reads_but_cannot_post_measurements(client, admin):
    asset_id = create_asset(client, admin)
    export = {"X-API-Key": make_key(client, admin, "finance-export", "export")["key"]}

    assert client.get("/assetslist", headers=export).status_code == 200
    response = client.post(f"/assets/{asset_id}/telemetry", headers=export, json=TELEMETRY)
    assert response.status_code == 403


def test_admin_key_configures_but_cannot_manage_keys(client, admin):
    admin_key = {"X-API-Key": make_key(client, admin, "automation", "admin")["key"]}
    create_asset(client, admin_key)
    assert client.get("/api-keys", headers=admin_key).status_code == 403


def test_revoked_key_is_rejected_on_its_next_call(client, admin):
    created = make_key(client, admin, "bridge-openems", "export")
    key = {"X-API-Key": created["key"]}
    assert client.get("/assetslist", headers=key).status_code == 200

    assert client.delete(f"/api-keys/{created['id']}", headers=admin).status_code == 204
    assert client.get("/assetslist", headers=key).status_code == 401
    assert client.delete(f"/api-keys/{created['id']}", headers=admin).status_code == 204  # twice is fine
    assert client.get("/api-keys", headers=admin).json()[0]["revoked_at"] is not None


def test_revoke_unknown_key_is_404(client, admin):
    assert client.delete("/api-keys/999999", headers=admin).status_code == 404


def test_key_use_is_recorded(client, admin, db_session):
    created = make_key(client, admin, "finance-export", "export")
    client.get("/assetslist", headers={"X-API-Key": created["key"]})
    assert db_session.get(models.ApiKey, created["id"]).last_used_at is not None


# --- The MVP's single API_KEY (until BE-09) ---

def test_legacy_api_key_still_works_with_full_access(client, auth_headers):
    asset_id = create_asset(client, auth_headers)
    assert client.get("/assetslist", headers=auth_headers).status_code == 200
    assert client.post(f"/assets/{asset_id}/telemetry", headers=auth_headers, json=TELEMETRY).status_code == 201


def test_legacy_api_key_is_refused_once_unset(client, auth_headers, monkeypatch):
    monkeypatch.setattr(config.settings, "legacy_api_key", None)
    assert client.get("/assetslist", headers=auth_headers).status_code == 401


# --- Configuration ---

def test_auth_cannot_be_disabled_outside_development():
    settings = config.Settings.from_env()
    settings.auth_enabled, settings.environment = False, "staging"
    with pytest.raises(RuntimeError, match="AUTH_ENABLED=false"):
        settings.validate()


def test_session_secret_is_required():
    settings = config.Settings.from_env()
    settings.session_secret = "too-short"
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        settings.validate()
