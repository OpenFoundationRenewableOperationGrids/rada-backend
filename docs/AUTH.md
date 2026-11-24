# Sign-in, roles and API keys (BE-04)

*[Français ci-dessous](#connexion-rôles-et-clés-api-be-04)*

RADA 1.0 signs people in with an email and a password, gives each account the role **admin** or **viewer**, and gives machines named **API keys** with a scope. The code is in the [`auth/`](../auth) package. It is built so that an identity server (rada-auth, OIDC, option 2 of the sign-in proposal) can replace the password part later without changing the endpoints (§7).

## §1 Who may do what

| Caller | Reads (GET, `/llm/ask`) | Telemetry and measurements | Configuration and writes | Users and API keys |
|---|---|---|---|---|
| User, `viewer` | yes | no | no | no |
| User, `admin` | yes | yes | yes | yes |
| API key, `ingest` | no | yes | no | no |
| API key, `export` | yes | no | no | no |
| API key, `admin` | yes | yes | yes | no |
| `API_KEY` (legacy, until BE-09) | yes | yes | yes | no |

Every protected route declares one dependency from [`auth/deps.py`](../auth/deps.py): `require_read`, `require_ingest`, `require_admin` or `require_admin_user`. New endpoints (BE-03, BE-05, BE-06) use the same ones: `GET` → `require_read`, `POST /sites/{id}/measurements` → `require_ingest`, any configuration change → `require_admin`.

## §2 API contract C1 (sign-in part of BE-15)

**Status: proposal, to be agreed with Candy.**

### Endpoints

| Method and path | Auth | Body | Success | Errors |
|---|---|---|---|---|
| `GET /auth/setup-status` | none | | `200 {"needs_setup": bool, "setup_token_required": bool}` | |
| `POST /auth/setup` | none (+ `X-Setup-Token` if configured) | `{"email", "password"}` | `201 {"user", "expires_at"}` + cookie | 403 setup token, 409 already set up, 422 |
| `POST /auth/login` | none | `{"email", "password"}` | `200 {"user", "expires_at"}` + cookie | 401, 422, 429 |
| `POST /auth/token` | none | `{"email", "password"}` | `200 {"user", "expires_at", "access_token", "token_type": "bearer", "expires_in"}`, no cookie | 401, 422, 429 |
| `POST /auth/logout` | session (optional) | | `204`, cookie cleared | |
| `GET /auth/me` | session | | `200 user` | 401, 403 for an API key |
| `POST /auth/password` | session | `{"current_password", "new_password"}` | `204`, every session ended | 401 wrong current password, 422 |
| `GET /users` | admin session | | `200 [user]` | 401, 403 |
| `POST /users` | admin session | `{"email", "password", "role"}` (role defaults to `viewer`) | `201 user` | 409 email taken, 422 |
| `PATCH /users/{id}` | admin session | any of `{"role", "is_active", "password"}` | `200 user` | 404, 409 last active admin, 422 |
| `GET /api-keys` | admin session | | `200 [key]` (never the key itself) | 401, 403 |
| `POST /api-keys` | admin session | `{"name", "scope"}` | `201 key + "key"` (shown once) | 409 name taken, 422 |
| `DELETE /api-keys/{id}` | admin session | | `204` (revoked; twice is fine) | 404 |

A `user` is `{"id", "email", "role", "auth_method", "is_active", "last_login_at", "created_at"}`. A `key` is `{"id", "name", "prefix", "scope", "created_by_id", "created_at", "last_used_at", "revoked_at"}`. Emails are stored lowercased.

### Errors

Always `{"detail": "<English message>"}`, as in the rest of the API (422 from request validation keeps FastAPI's list of errors).

| Status | Meaning | Front end |
|---|---|---|
| 401 | No credentials, wrong credentials, session expired or ended, revoked key. Carries `WWW-Authenticate: Bearer` | Go to the sign-in screen |
| 403 | Signed in, but not allowed (viewer on an admin route, key scope too narrow, cookie write from another origin) | Show "not allowed" |
| 409 | Setup already done, email or key name taken, last active admin | Show the message |
| 422 | Invalid email, password rule broken, invalid field | Show the message next to the field |
| 429 | Too many failed sign-ins; `Retry-After` gives the seconds to wait | Show the wait |

A wrong email and a wrong password give the same 401 message: `Invalid email or password`.

### Session

- A session is a token signed with HS256 (PyJWT), valid `SESSION_TTL_HOURS` (8 by default). It is not refreshed: the user signs in again when it expires.
- **Web:** `POST /auth/login` sets the cookie `rada_session` (`HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`). The browser never sees the token.
- **Native apps:** `POST /auth/token` returns the token in the body, to keep in secure storage (`expo-secure-store`) and send as `Authorization: Bearer <token>`. Same accounts, roles and throttling.
- **Sign-out ends every session of the user**, web and apps. So do a password change, a reset by an administrator and a deactivation. Roles are read from the database on every request: a promotion or demotion applies at once.

### How the front end's server-side proxy passes the cookie

Proposed: the browser only talks to the Next.js server, which calls the API.

1. On sign-in, the Next.js route handler calls `POST /auth/login`, reads `Set-Cookie: rada_session=…` from the response and sets the same cookie (same attributes) on the front end's own domain.
2. On every API call, the Next.js server reads `rada_session` from the incoming request and forwards it as `Cookie: rada_session=<value>`, or as `Authorization: Bearer <value>` (both are accepted).
3. On sign-out, it calls `POST /auth/logout` with the cookie, then deletes its own cookie.
4. A 401 from the API means: delete the cookie and redirect to sign-in.

The server-side proxy sends no `Origin` header (or one in `CORS_ORIGINS`), so the CSRF check below never blocks it. If the browser calls the API directly instead, the API's own cookie works as is, with `credentials: "include"`.

### Cross-site protection

With the cookie, a request that changes something (`POST`, `PUT`, `PATCH`, `DELETE`) is refused (403) when its `Origin` header is not in `CORS_ORIGINS`. `SameSite=Lax` already stops most cross-site requests; this covers the rest. Bearer tokens and API keys are not sent automatically by a browser, so they are not checked.

## §3 Passwords and failed sign-ins

Following NIST SP 800-63B-4, for a password that is the only factor:

- at least **15 characters**, up to 256; long passphrases are welcome;
- **no composition rules** (no forced digits or symbols) and no forced periodic change;
- refused: fewer than 5 different characters, or containing the email address;
- stored as **Argon2id** (pwdlib); a hash with outdated parameters is upgraded at the next sign-in.

Checking new passwords against lists of breached passwords is not in 1.0.

Failed sign-ins are counted per email (5 free failures) and per client address (30). After that, each new failure doubles the wait (1 s, 2 s, 4 s… up to 15 minutes), answered with 429 and `Retry-After`. Fifteen quiet minutes forget the failures. The counters live in the API process; RADA runs one uvicorn process.

Behind Traefik, set `FORWARDED_ALLOW_IPS` (read by uvicorn) to the proxy's address, or `*` if only the proxy can reach the API. Without it, every client has Traefik's address, and 30 failures from anyone would make everyone wait.

## §4 API keys

- A key is `rada_` followed by 43 random characters. It is shown **once**, in the response to `POST /api-keys`. RADA stores its SHA-256 and its first 12 characters (`prefix`), to recognise it in a list.
- One key per integration, with a unique name, for example `bridge-openems` (scope `ingest`).
- Revoking (`DELETE /api-keys/{id}`) refuses the key from its next request. The row is kept, with `revoked_at`.
- `last_used_at` is updated at most once a minute.
- Only a signed-in administrator manages keys and users: no API key can create another key or an account.

## §5 First run

On a new installation, `GET /auth/setup-status` returns `"needs_setup": true` and the front end shows the first-run screen. `POST /auth/setup` creates the first administrator and signs them in. It works once: afterwards it answers 409. No default user, password or key is shipped.

Until the first administrator exists, whoever reaches the API first can claim it. To prevent that on a public server, set `SETUP_TOKEN`: the call then needs the `X-Setup-Token` header with that value.

## §6 Configuration

| Variable | Default | |
|---|---|---|
| `AUTH_ENABLED` | `true` | `false` makes every request an administrator; refused unless `ENVIRONMENT=development` |
| `SESSION_SECRET` | none | **Required** when auth is on, 32+ characters. Changing it ends every session |
| `SESSION_TTL_HOURS` | `8` | |
| `COOKIE_SECURE` | `true` | `false` only for local http |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated |
| `SETUP_TOKEN` | none | Optional, see §5 |
| `FORWARDED_ALLOW_IPS` | uvicorn's | See §3 |
| `SIMULATOR_API_KEY` | none | The telemetry simulator's key (`admin` scope); falls back to `API_KEY` |
| `API_KEY` | none | **Deprecated**, see below |

**Switching from the single `API_KEY`.** The MVP's key is still accepted, with the access of an `admin` key, while it is set. To switch:

1. Create the first administrator, then an `ingest` key for the bridge (`bridge-openems`) and, if the telemetry simulator runs, an `admin` key for it in `SIMULATOR_API_KEY` (it reads the asset list and posts telemetry).
2. Put the new key in the bridge's `RADA_API_KEY` and restart it.
3. Once the front end signs in with sessions, empty `API_KEY` and restart the API. BE-09 removes the code before the release.

## §7 Later: an identity server (option 2)

If the team adopts rada-auth (Keycloak or ZITADEL, OIDC), only the session part changes:

- `decode_session_token` ([`auth/security.py`](../auth/security.py)) checks rada-auth's tokens against its published keys (`jwt.PyJWKClient`): signature, issuer, audience, expiry.
- `get_principal` ([`auth/deps.py`](../auth/deps.py)) finds the user by `external_subject` (`auth_method = sso`), creating them on first sign-in, and maps the token's role claim to admin or viewer.
- `/auth/login`, `/auth/token`, `/auth/setup` and `/auth/password` go away; passwords, the second step (passkeys, TOTP) and customer SSO move to rada-auth.
- The `require_*` dependencies, the roles, the API keys and every endpoint stay as they are.

## §8 Pre-production check

1. On an empty database, `GET /auth/setup-status` → `needs_setup: true`; `POST /auth/setup` creates the administrator.
2. As the administrator, `POST /users` creates a viewer. Signed in as the viewer, `GET /assetslist` → 200 and `POST /assets` → **403**.
3. Create an `export` key; posting a batch of measurements with it → **403**.
4. Revoke a key (`DELETE /api-keys/{id}`); its next call → **401**.

---

# Connexion, rôles et clés API (BE-04)

RADA 1.0 connecte les personnes par e-mail et mot de passe, donne à chaque compte le rôle **administrateur** (`admin`) ou **lecteur** (`viewer`), et donne aux machines des **clés API** nommées, chacune avec une portée. Le code est dans le package [`auth/`](../auth). Il est prévu pour qu'un serveur d'identité (rada-auth, OIDC, option 2 de la proposition de connexion) puisse remplacer plus tard la partie mot de passe sans changer les endpoints (§7). Les tableaux détaillés sont ceux de la partie anglaise ci-dessus.

## §1 Qui peut faire quoi

- **Lecteur :** lit tout (GET, `/llm/ask`). Ne modifie rien.
- **Administrateur :** lit, écrit, modifie la configuration, gère les utilisateurs et les clés API.
- **Clé `ingest` :** envoie la télémétrie et les mesures (le bridge). Ne lit pas.
- **Clé `export` :** lit les données. N'envoie rien.
- **Clé `admin` :** tout ce que fait un administrateur, sauf gérer les utilisateurs et les clés.
- **`API_KEY` (ancienne clé unique) :** comme une clé `admin`, jusqu'à BE-09.

Chaque route protégée déclare une dépendance de [`auth/deps.py`](../auth/deps.py) : `require_read`, `require_ingest`, `require_admin` ou `require_admin_user`. Les nouveaux endpoints de BE-03, BE-05 et BE-06 utilisent les mêmes.

## §2 Contrat d'API C1 (partie connexion de BE-15)

**Statut : proposition, à valider avec Candy.** Les endpoints, les corps et les codes sont dans le tableau anglais.

- **Erreurs :** toujours `{"detail": "<message en anglais>"}`. 401 : pas ou mauvais identifiants, session expirée ou terminée, clé révoquée (retour à l'écran de connexion). 403 : connecté mais pas autorisé. 409 : installation déjà faite, e-mail ou nom de clé déjà pris, dernier administrateur actif. 422 : champ invalide ou règle de mot de passe. 429 : trop d'échecs de connexion, attendre `Retry-After` secondes. Un mauvais e-mail et un mauvais mot de passe donnent le même message.
- **Session :** jeton signé HS256 (PyJWT), valable `SESSION_TTL_HOURS` (8 h par défaut), sans renouvellement.
- **Web :** `POST /auth/login` pose le cookie `rada_session` (`HttpOnly`, `Secure`, `SameSite=Lax`). Le navigateur ne voit jamais le jeton.
- **Applications natives :** `POST /auth/token` renvoie le jeton dans la réponse, à garder dans `expo-secure-store` et à envoyer en `Authorization: Bearer <jeton>`. Mêmes comptes, mêmes rôles, même ralentissement.
- **La déconnexion termine toutes les sessions de l'utilisateur**, web et applications. Le changement de mot de passe, sa réinitialisation par un administrateur et la désactivation du compte aussi. Le rôle est relu en base à chaque requête.

**Transmission du cookie par le proxy côté serveur du front (proposition).** Le navigateur ne parle qu'au serveur Next.js. À la connexion, Next.js appelle `POST /auth/login`, lit `Set-Cookie: rada_session=…` et pose le même cookie sur son propre domaine. À chaque appel, il renvoie la valeur à l'API en `Cookie: rada_session=<valeur>` ou en `Authorization: Bearer <valeur>`. À la déconnexion, il appelle `POST /auth/logout` puis supprime son cookie. Un 401 signifie : supprimer le cookie et rediriger vers la connexion.

**Protection inter-sites :** avec le cookie, une requête qui modifie quelque chose est refusée (403) si son en-tête `Origin` n'est pas dans `CORS_ORIGINS`. Le proxy côté serveur n'envoie pas d'`Origin` et n'est donc pas bloqué.

## §3 Mots de passe et échecs de connexion

Selon NIST SP 800-63B-4, pour un mot de passe seul facteur : au moins **15 caractères** (jusqu'à 256), **aucune règle de composition**, aucun changement forcé. Refusés : moins de 5 caractères différents, ou contenant l'adresse e-mail. Stockés en **Argon2id** (pwdlib). La vérification contre les listes de mots de passe divulgués n'est pas dans la 1.0.

Les échecs sont comptés par e-mail (5 gratuits) et par adresse client (30). Ensuite, chaque nouvel échec double l'attente (1 s, 2 s, 4 s… jusqu'à 15 minutes), avec une réponse 429 et `Retry-After`. Derrière Traefik, renseigner `FORWARDED_ALLOW_IPS` (lu par uvicorn) ; sinon tous les clients ont l'adresse de Traefik, et 30 échecs de n'importe qui feraient attendre tout le monde.

## §4 Clés API

Une clé est `rada_` suivi de 43 caractères aléatoires, affichée **une seule fois** à la création. RADA n'en garde que le SHA-256 et les 12 premiers caractères (`prefix`). Une clé par intégration, au nom unique (par exemple `bridge-openems`, portée `ingest`). La révocation (`DELETE /api-keys/{id}`) refuse la clé dès sa requête suivante ; la ligne est conservée. Seul un administrateur connecté gère les clés et les utilisateurs.

## §5 Première installation

Sur une nouvelle installation, `GET /auth/setup-status` renvoie `"needs_setup": true` et le front affiche l'écran de première installation. `POST /auth/setup` crée le premier administrateur et le connecte, une seule fois (409 ensuite). Aucun utilisateur, mot de passe ou clé par défaut n'est livré. Sur un serveur public, renseigner `SETUP_TOKEN` : l'appel exige alors l'en-tête `X-Setup-Token`.

## §6 Configuration

Variables : `AUTH_ENABLED`, `SESSION_SECRET` (**obligatoire**, 32 caractères ou plus), `SESSION_TTL_HOURS`, `COOKIE_SECURE`, `CORS_ORIGINS`, `SETUP_TOKEN`, `FORWARDED_ALLOW_IPS`, et `API_KEY` (**dépréciée**). Voir le tableau anglais.

**Bascule depuis l'`API_KEY` unique :** créer le premier administrateur, puis une clé `ingest` pour le bridge et, si le simulateur tourne, une clé `admin` dans `SIMULATOR_API_KEY` (il lit la liste des actifs et poste la télémétrie) ; mettre la nouvelle clé dans `RADA_API_KEY` du bridge ; une fois le front passé aux sessions, vider `API_KEY` et redémarrer l'API. BE-09 retire le code avant la version.

## §7 Plus tard : un serveur d'identité (option 2)

Si l'équipe adopte rada-auth, seule la partie session change : `decode_session_token` vérifie les jetons de rada-auth avec ses clés publiées (JWKS), `get_principal` retrouve l'utilisateur par `external_subject` et traduit le rôle du jeton. Les routes de mot de passe disparaissent. Les dépendances `require_*`, les rôles, les clés API et les endpoints ne changent pas.

## §8 Vérification en préproduction

1. Base vide : `GET /auth/setup-status` → `needs_setup: true` ; `POST /auth/setup` crée l'administrateur.
2. L'administrateur crée un lecteur (`POST /users`). Connecté en lecteur : `GET /assetslist` → 200, `POST /assets` → **403**.
3. Créer une clé `export` ; envoyer un lot de mesures avec elle → **403**.
4. Révoquer une clé ; son appel suivant → **401**.
