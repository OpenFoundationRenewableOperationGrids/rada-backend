# Guide des tests — rada-backend

Ce document explique comment sont organisés les tests de l'API, comment les lancer,
et comment en écrire de nouveaux, à partir d'exemples réels tirés de
[tests/test_assets_crud.py](tests/test_assets_crud.py).

## 1. Stack de test

- **pytest** — exécuteur de tests
- **FastAPI `TestClient`** (basé sur `httpx`) — envoie de vraies requêtes HTTP à l'application
  sans lancer de serveur
- **SQLite en mémoire** — remplace Postgres pendant les tests, pour rester rapide et isolé

Aucun test ne touche la base Postgres de développement/production : la session DB utilisée par
l'API est remplacée ("overridée") par une session SQLite créée pour l'occasion.

## 2. Où sont les fichiers

```
tests/
  conftest.py           # fixtures partagées (client, db_session, reset_db)
  test_assets_crud.py   # tests des routes POST/PUT/PATCH /assets
```

Tout nouveau fichier de test doit être placé dans `tests/` et nommé `test_*.py` pour être
détecté automatiquement par pytest.

## 3. `conftest.py` expliqué

```python
import os
import sys

os.environ.setdefault("AUTH_ENABLED", "false")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from database import Base
from models import Asset
import main

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


main.app.dependency_overrides[main.get_db] = _override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.create_all(bind=test_engine, tables=[Asset.__table__])
    yield
    Base.metadata.drop_all(bind=test_engine, tables=[Asset.__table__])


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
```

Points clés :

| Élément | Rôle |
|---|---|
| `AUTH_ENABLED=false` | Désactive la vérification de la clé API (`X-API-Key`) pendant les tests, pour ne pas avoir à la simuler dans chaque requête |
| `sys.path.insert(...)` | Permet d'`import main` depuis `tests/` même si pytest est lancé depuis un autre dossier |
| `test_engine` (SQLite `StaticPool`) | Une seule connexion SQLite partagée en mémoire pour toute la durée du process de test |
| `main.app.dependency_overrides[main.get_db]` | Remplace la dépendance FastAPI `get_db` (qui pointe normalement vers Postgres) par la session SQLite de test — **c'est ce qui isole les tests de la vraie base** |
| `reset_db` (`autouse=True`) | Recrée les tables avant chaque test et les supprime après, pour qu'aucun test ne voie les données laissées par un autre |
| `client` | Fixture prête à l'emploi injectée dans un test via son nom en paramètre |
| `db_session` | Donne un accès direct à la base de test, utile pour vérifier ce que l'API a réellement écrit en base |

> ⚠️ **Piège SQLite** : `reset_db` ne crée que la table `Asset` (`tables=[Asset.__table__]`), pas
> tout `Base.metadata`. Certains modèles du projet (ex. `StateOfCharge`, qui a une clé primaire
> composite `id` + `timestamp`) ne sont pas compatibles avec l'auto-increment SQLite et font
> planter `create_all()` si on les inclut. Si vous ajoutez des tests sur un nouveau modèle,
> ajoutez sa table à la liste `tables=[...]` plutôt que de repasser par `Base.metadata.create_all()`
> sans filtre.

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
def test_create_asset_success(client, db_session):
    response = client.post("/assets", json=make_asset_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["action"] == "created"
    assert isinstance(body["asset_id"], int)

    # Vérification directe en base, pas seulement via la réponse HTTP
    asset = db_session.query(Asset).filter(Asset.id == body["asset_id"]).first()
    assert asset is not None
    assert asset.eic_code == "10T-FR-BATT-01"
```

- `client` et `db_session` sont injectés automatiquement par pytest car ce sont des noms de
  fixtures déclarées dans `conftest.py`.
- On appelle l'endpoint **exactement comme le ferait un vrai client HTTP** (`client.post(...)`,
  `client.put(...)`, `client.patch(...)`), avec un corps JSON.
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

## 6. Les 4 scénarios à tester pour chaque route CRUD

Chaque route `POST` / `PUT` / `PATCH` de ce projet suit le même schéma de test, illustré ici
pour `PUT /assets/{asset_id}` :

### a) Cas de succès (200/201)

```python
def test_replace_asset_success(client, db_session):
    created = client.post("/assets", json=make_asset_payload()).json()
    asset_id = created["asset_id"]

    response = client.put(
        f"/assets/{asset_id}",
        json=make_asset_payload(name="Battery One Renamed", max_capacity_mwh=20.0),
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
def test_replace_asset_not_found_returns_404(client):
    response = client.put("/assets/999999", json=make_asset_payload())
    assert response.status_code == 404
```

### c) Conflit métier (409) — ici, un `eic_code` déjà utilisé par un autre asset

```python
def test_replace_asset_eic_code_conflict_returns_409(client):
    client.post("/assets", json=make_asset_payload())  # eic_code = 10T-FR-BATT-01
    second = client.post(
        "/assets", json=make_asset_payload(eic_code="10T-FR-BATT-02", name="Battery Two")
    ).json()

    response = client.put(
        f"/assets/{second['asset_id']}",
        json=make_asset_payload(eic_code="10T-FR-BATT-01", name="Battery Two"),
    )

    assert response.status_code == 409
```

### d) Validation du payload (422) — champ requis manquant

```python
def test_replace_asset_missing_field_returns_422(client):
    created = client.post("/assets", json=make_asset_payload()).json()
    payload = make_asset_payload()
    del payload["max_charge_rate_mw"]

    response = client.put(f"/assets/{created['asset_id']}", json=payload)
    assert response.status_code == 422
```

Ce dernier cas fonctionne "gratuitement" grâce à Pydantic : dès qu'un champ obligatoire du
schéma (`AssetUpdate`, `AssetCreate`...) est absent ou du mauvais type, FastAPI renvoie un 422
avant même d'exécuter le code de la route — pas besoin de le coder à la main.

## 7. Cas particulier du PATCH : mise à jour partielle

Le PATCH utilise un schéma où **tous les champs sont optionnels** (`AssetPatch`). Le test doit
donc vérifier que les champs *non envoyés* restent inchangés :

```python
def test_patch_asset_partial_update(client, db_session):
    created = client.post("/assets", json=make_asset_payload()).json()
    asset_id = created["asset_id"]

    response = client.patch(f"/assets/{asset_id}", json={"name": "Battery One Patched"})

    assert response.status_code == 200

    db_session.expire_all()
    asset = db_session.query(Asset).filter(Asset.id == asset_id).first()
    assert asset.name == "Battery One Patched"
    assert asset.max_capacity_mwh == 10.0        # inchangé
    assert asset.eic_code == "10T-FR-BATT-01"    # inchangé
```

Et qu'un corps vide (`{}`) ne modifie rien (`test_patch_asset_empty_body_is_noop`).

## 8. Ajouter un test pour une nouvelle route

1. Créer (ou compléter) un fichier `tests/test_<ressource>.py`.
2. Utiliser les fixtures `client` et, si besoin, `db_session` en paramètres de la fonction de
   test — pytest les injecte automatiquement par leur nom.
3. Si la route touche un nouveau modèle SQLAlchemy, ajouter sa table dans le `tables=[...]` de
   `reset_db` (voir l'avertissement de la section 3) pour que la table existe dans la base de
   test SQLite.
4. Couvrir au minimum : le cas de succès, un cas d'erreur métier propre à la route (404/409...),
   et un cas de validation (422) si la route accepte un body.
5. Lancer `python -m pytest tests/test_<ressource>.py -v` pour vérifier.

## 9. Ce que ces tests ne couvrent pas

- Le comportement réel avec **Postgres** (types spécifiques, contraintes, `SET TIME ZONE`) —
  SQLite est une approximation pratique mais pas 100% fidèle.
- L'authentification par clé API, désactivée ici via `AUTH_ENABLED=false`. Si un changement
  touche `verify_api_key`, il faut le tester séparément avec `AUTH_ENABLED=true` et un client
  qui envoie (ou omet) l'en-tête `X-API-Key`.
