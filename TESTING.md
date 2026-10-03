# Guide des tests — rada-backend

Ce document explique comment sont organisés les tests de l'API, comment les lancer,
et comment en écrire de nouveaux, à partir d'exemples réels tirés de
[tests/test_assets_crud.py](tests/test_assets_crud.py) et
[tests/test_assets_gps_api.py](tests/test_assets_gps_api.py).

## 1. Stack de test

- **pytest** — exécuteur de tests
- **FastAPI `TestClient`** (basé sur `httpx`) — envoie de vraies requêtes HTTP à l'application
  sans lancer de serveur
- **PostgreSQL** — une base **dédiée aux tests**, `rada_test`, séparée de la base de
  développement (`rada_dev`). Les tests ne touchent jamais les données de dev.

`rada_test` doit exister sur le même serveur Postgres que `DATABASE_URL` (même utilisateur/hôte,
nom de base différent) avant de lancer les tests — voir section 9.

## 2. Où sont les fichiers

```
tests/
  conftest.py            # fixtures partagées (client, db_session, auth_headers, clean_tables)
  test_assets_crud.py    # tests des routes POST/PUT/PATCH /assets
  test_assets_gps_api.py # tests d'intégration GPS/edge_id sur les endpoints assets
  test_asset_schema.py   # tests unitaires purs du schéma Pydantic AssetCreate (pas de DB/HTTP)
```

Tout nouveau fichier de test doit être placé dans `tests/` et nommé `test_*.py` pour être
détecté automatiquement par pytest.

## 3. `conftest.py` expliqué

```python
import os
from dotenv import load_dotenv

load_dotenv()

_dev_url = os.environ["DATABASE_URL"]
os.environ["DATABASE_URL"] = _dev_url.rsplit("/", 1)[0] + "/rada_test"
os.environ["API_KEY"] = "test-api-key"
os.environ["AUTH_ENABLED"] = "true"
os.environ["TELEMETRY_SIMULATOR"] = "false"
os.environ["ENVIRONMENT"] = "development"

import pytest
from fastapi.testclient import TestClient

import database
import models  # noqa: F401 — registers all tables on database.Base.metadata

database.Base.metadata.drop_all(bind=database.engine)
database.Base.metadata.create_all(bind=database.engine)

import main

API_KEY = os.environ["API_KEY"]


@pytest.fixture()
def db_session():
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clean_tables():
    session = database.SessionLocal()
    try:
        session.execute(models.DispatchCommand.__table__.delete())
        session.execute(models.StateOfCharge.__table__.delete())
        session.execute(models.Asset.__table__.delete())
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def auth_headers():
    return {"X-API-Key": API_KEY}
```

Points clés :

| Élément | Rôle |
|---|---|
| `os.environ["DATABASE_URL"] = ... + "/rada_test"` | Redirige l'app vers la base de test **avant** d'importer `main`/`database`, quel que soit le contenu du `.env` du développeur |
| `os.environ["API_KEY"] / ["AUTH_ENABLED"] = "true"` | Force une clé API connue et **active réellement l'authentification** pendant les tests (contrairement à un simple bypass) |
| `Base.metadata.drop_all` puis `create_all` | Repart d'un schéma propre à chaque lancement de la suite (pas juste par test) |
| `db_session` | Accès direct à la base de test, pour arranger ou vérifier l'état en base indépendamment des réponses HTTP |
| `clean_tables` (`autouse=True`) | Vide les tables `assets`/`state_of_charge`/`dispatch_commands` avant **chaque** test pour qu'ils ne se polluent pas entre eux |
| `client` | `TestClient` FastAPI prêt à l'emploi, injecté par son nom dans un test |
| `auth_headers` | Dict `{"X-API-Key": "test-api-key"}` à passer dans `headers=` de chaque requête vers une route protégée |

> ⚠️ **Ne pas oublier `headers=auth_headers`** : comme `AUTH_ENABLED=true` dans les tests, tout
> appel à une route protégée par `verify_api_key` (toutes sauf `/`, `/health`) sans cet en-tête
> renvoie `403`, pas le code attendu par le test.

## 4. Lancer les tests

```bash
# Tous les tests
python -m pytest

# Un seul fichier
python -m pytest tests/test_assets_crud.py

# Un seul test, avec détail (-v)
python -m pytest tests/test_assets_crud.py::test_create_asset_success -v
```

Sous Windows avec l'environnement virtuel du projet :

```bash
venv/Scripts/python.exe -m pytest -v
```

## 5. Anatomie d'un test — exemple commenté

```python
def test_create_asset_success(client, db_session, auth_headers):
    response = client.post("/assets", json=make_asset_payload(), headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["action"] == "created"
    assert isinstance(body["asset_id"], int)

    # Vérification directe en base, pas seulement via la réponse HTTP
    asset = db_session.query(Asset).filter(Asset.id == body["asset_id"]).first()
    assert asset is not None
    assert asset.eic_code == "10T-FR-BATT-01"
```

- `client`, `db_session` et `auth_headers` sont injectés automatiquement par pytest car ce sont
  des noms de fixtures déclarées dans `conftest.py`.
- On appelle l'endpoint **exactement comme le ferait un vrai client HTTP** (`client.post(...)`,
  `client.put(...)`, `client.patch(...)`), avec un corps JSON et l'en-tête `X-API-Key`.
- On vérifie deux choses séparément : la **réponse HTTP** (code + JSON) et **l'état réel en
  base** via `db_session`. C'est important car un endpoint pourrait renvoyer un 200 sans avoir
  correctement persisté les données.

### Le helper `make_asset_payload`

Pour éviter de répéter un objet JSON complet dans chaque test, on utilise un petit constructeur
avec des valeurs par défaut, surchargeables au besoin :

```python
def make_asset_payload(**overrides):
    payload = {
        "eic_code": "10T-FR-BATT-01",
        "name": "Battery One",
        "asset_type": "battery",
        "max_capacity_mwh": 10.0,
        "max_charge_rate_mw": 2.0,
        "max_discharge_rate_mw": 2.0,
    }
    payload.update(overrides)
    return payload
```

Usage :

```python
make_asset_payload()                          # payload par défaut valide
make_asset_payload(name="Battery Two")        # un seul champ modifié
make_asset_payload(eic_code="10T-FR-BATT-02")
```

`tests/test_assets_gps_api.py` suit le même principe avec `ASSET_PAYLOAD` et une fonction
`create_asset(client, auth_headers, **overrides)`.

## 6. Les 4 scénarios à tester pour chaque route CRUD

Chaque route `POST` / `PUT` / `PATCH` de ce projet suit le même schéma de test, illustré ici
pour `PUT /assets/{asset_id}` :

### a) Cas de succès (200/201)

```python
def test_replace_asset_success(client, db_session, auth_headers):
    created = client.post("/assets", json=make_asset_payload(), headers=auth_headers).json()
    asset_id = created["asset_id"]

    response = client.put(
        f"/assets/{asset_id}",
        json=make_asset_payload(name="Battery One Renamed", max_capacity_mwh=20.0),
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["action"] == "updated"

    db_session.expire_all()  # force SQLAlchemy à relire les données depuis la DB
    asset = db_session.query(Asset).filter(Asset.id == asset_id).first()
    assert asset.name == "Battery One Renamed"
```

> `db_session.expire_all()` est nécessaire après une écriture faite par une **autre** session
> (celle utilisée en interne par la requête HTTP) : sans ça, SQLAlchemy pourrait renvoyer des
> objets mis en cache avec les anciennes valeurs.

### b) Ressource introuvable (404)

```python
def test_replace_asset_not_found_returns_404(client, auth_headers):
    response = client.put("/assets/999999", json=make_asset_payload(), headers=auth_headers)
    assert response.status_code == 404
```

### c) Conflit métier (409) — ici, un `eic_code` déjà utilisé par un autre asset

```python
def test_replace_asset_eic_code_conflict_returns_409(client, auth_headers):
    client.post("/assets", json=make_asset_payload(), headers=auth_headers)  # eic_code = 10T-FR-BATT-01
    second = client.post(
        "/assets", json=make_asset_payload(eic_code="10T-FR-BATT-02", name="Battery Two"), headers=auth_headers
    ).json()

    response = client.put(
        f"/assets/{second['asset_id']}",
        json=make_asset_payload(eic_code="10T-FR-BATT-01", name="Battery Two"),
        headers=auth_headers,
    )

    assert response.status_code == 409
```

### d) Validation du payload (422) — champ requis manquant

```python
def test_replace_asset_missing_field_returns_422(client, auth_headers):
    created = client.post("/assets", json=make_asset_payload(), headers=auth_headers).json()
    payload = make_asset_payload()
    del payload["max_charge_rate_mw"]

    response = client.put(f"/assets/{created['asset_id']}", json=payload, headers=auth_headers)
    assert response.status_code == 422
```

Ce dernier cas fonctionne "gratuitement" grâce à Pydantic : dès qu'un champ obligatoire du
schéma (`AssetUpdate`, `AssetCreate`...) est absent ou du mauvais type, FastAPI renvoie un 422
avant même d'exécuter le code de la route — pas besoin de le coder à la main.

## 7. Cas particulier du PATCH : mise à jour partielle

Le PATCH utilise un schéma où **tous les champs sont optionnels** (`AssetPatch`). Le test doit
donc vérifier que les champs *non envoyés* restent inchangés :

```python
def test_patch_asset_partial_update(client, db_session, auth_headers):
    created = client.post("/assets", json=make_asset_payload(), headers=auth_headers).json()
    asset_id = created["asset_id"]

    response = client.patch(f"/assets/{asset_id}", json={"name": "Battery One Patched"}, headers=auth_headers)

    assert response.status_code == 200

    db_session.expire_all()
    asset = db_session.query(Asset).filter(Asset.id == asset_id).first()
    assert asset.name == "Battery One Patched"
    assert asset.max_capacity_mwh == 10.0        # inchangé
    assert asset.eic_code == "10T-FR-BATT-01"    # inchangé
```

Et qu'un corps vide (`{}`) ne modifie rien (`test_patch_asset_empty_body_is_noop`).

`test_assets_gps_api.py::test_patch_overwrites_gps_coordinates` applique le même principe aux
champs GPS (`latitude`/`longitude`/`edge_id`) : puisque `POST /assets` ne fait plus d'upsert
(voir section 8), c'est désormais un `PATCH` qui sert à corriger la position d'un asset déjà
créé.

## 8. Historique : pourquoi `POST /assets` n'est plus un upsert

`POST /assets` créait auparavant un asset **ou** le mettait à jour s'il existait déjà (upsert
basé sur `eic_code`). Cette ambiguïté a été supprimée : `POST` ne fait plus que créer (409 si le
`eic_code` existe déjà), et toute modification passe par `PUT`/`PATCH /assets/{asset_id}`. Les
tests qui vérifiaient l'ancien comportement d'upsert (dans `test_assets_gps_api.py`) ont été
adaptés en conséquence.

## 9. Ajouter un test pour une nouvelle route

1. Créer (ou compléter) un fichier `tests/test_<ressource>.py`.
2. Utiliser les fixtures `client`, `auth_headers` et, si besoin, `db_session` en paramètres de
   la fonction de test — pytest les injecte automatiquement par leur nom.
3. Couvrir au minimum : le cas de succès, un cas d'erreur métier propre à la route (404/409...),
   et un cas de validation (422) si la route accepte un body.
4. S'assurer que la base `rada_test` existe (une seule fois, pas par test) :
   ```bash
   psql -U rada_user -h localhost -c "CREATE DATABASE rada_test;"
   ```
   `conftest.py` se charge de (re)créer les tables à chaque lancement de la suite.
5. Lancer `python -m pytest tests/test_<ressource>.py -v` pour vérifier.

## 10. Ce que ces tests ne couvrent pas

- Le comportement du simulateur de télémétrie (`TELEMETRY_SIMULATOR` est forcé à `false`
  pendant les tests).
- Les erreurs de connexion à Postgres elles-mêmes : si `rada_test` n'existe pas ou est
  injoignable, `conftest.py` échoue dès l'import (`database.Base.metadata.create_all`), avant
  même de lancer un test.
