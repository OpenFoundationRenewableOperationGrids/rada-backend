# Database migrations

*[Français ci-dessous](#migrations-de-la-base-de-données)*

The schema is managed by [Alembic](https://alembic.sqlalchemy.org/). `models.py` and the revisions in `alembic/versions/` describe the same schema, and CI fails when they drift apart. Nothing else creates or changes tables: `create_all()`, `create_tables.py` and the hand-run SQL files are gone.

Alembic reads `DATABASE_URL` (from the environment, or `.env`) through `database.py`, exactly like the API. The API container runs `alembic upgrade head` before it starts uvicorn.

## Everyday commands

| Task | Command |
|---|---|
| Apply pending migrations | `alembic upgrade head` |
| Which revision is this database at? | `alembic current` |
| List the revisions | `alembic history` |
| Do the models and the migrations agree? | `alembic check` |
| Print the SQL without running it (for review) | `alembic upgrade head --sql` |
| Undo the last revision | `alembic downgrade -1` |

## Changing the schema

1. Edit `models.py`.
2. Bring your database to `head`, then generate a revision:
   ```bash
   alembic revision --autogenerate --rev-id 0002 -m "short description"
   ```
   Pass `--rev-id` with the next number. Files are named `<rev>_<slug>.py` (`file_template` in `alembic.ini`), so without it the name starts with a random hash and the revisions no longer list in order.
3. Read the generated file. Autogenerate misses some changes and gets others wrong:
   - data migrations (filling or moving rows) are always written by hand;
   - enum types: `drop_table` leaves them behind, so `downgrade()` must drop them, and a new value needs `ALTER TYPE … ADD VALUE`. The enums store the member **names** (`'BATTERY'`, not `'battery'`);
   - TimescaleDB objects (hypertables, continuous aggregates) are written with `op.execute`;
   - a renamed column or table is seen as a drop plus an add.
4. Write a `downgrade()` that really undoes `upgrade()`.
5. Run `alembic upgrade head`, `alembic downgrade -1`, `alembic upgrade head`, then `python -m pytest`. The tests build `rada_test` from the migrations and include the `alembic check`.
6. Commit the model change and its revision together. A schema migration is reviewed individually before merge (`DATA_MODEL.md` §6).

If two branches each add a revision on the same parent, `test_single_head` fails once both are merged. Rebase the later branch and set its `down_revision` to the other revision.

## Local TimescaleDB

A local PostgreSQL may not have TimescaleDB. Use the pinned container on port **5434** (chosen so it doesn't clash with a local PostgreSQL on 5432):

```bash
docker run -d --name rada-tsdb -p 5434:5432 -e POSTGRES_USER=rada -e POSTGRES_PASSWORD=rada -e POSTGRES_DB=rada -v rada-tsdb-data:/var/lib/postgresql/data timescale/timescaledb:2.32.0-pg16
docker exec rada-tsdb psql -U rada -d postgres -c "create database rada_test"
```

Then set `DATABASE_URL=postgresql://rada:rada@localhost:5434/rada` in `.env`, and run `alembic upgrade head`. The tests use `rada_test` on the same server.

**The image tag is pinned by hand.** `timescale/timescaledb:2.32.0-pg16` appears in two places: `.github/workflows/ci.yml` and the command above. Dependabot does not bump it (it only updates the pip packages and the `uses:` lines of the workflows), so update both together, and keep the PostgreSQL major version (`pg16`) matching production.

## One-off upgrade of an existing database (preprod, production)

Databases created before Alembic already have the baseline schema (revision `0001`), but no `alembic_version` table. They are **stamped** to the baseline, never upgraded through it.

> **Stamp before the new image starts.** If the new API container starts on a database that is not stamped, `alembic upgrade head` tries to create the baseline schema again and fails (the first error is `type "assettype" already exists`), and the API does not start. Nothing is changed (the migration runs in one transaction), but the API stays down until the database is stamped.

1. Back up the database.
2. Check the time zone of the existing timestamps. Revision 0002 converts `assets.created_at` and `updated_at` to `timestamptz`, reading them as UTC. The app has written them in UTC since 31 May 2026; only rows written before that, or outside the app, on a server whose time zone is not UTC, could be local time:
   ```sql
   SHOW timezone;
   SELECT min(created_at), max(updated_at) FROM assets;
   ```
   If the server is not UTC and `min(created_at)` is before `2026-05-31 16:00`, stop and check those rows before going on. (Preprod and production were reseeded on 17 October 2026.)
3. Check that the columns of the old `migrations/001_assets_gps_edge.sql` are there. Stamping records that the database has the baseline schema, so it must really have it:
   ```sql
   SELECT column_name FROM information_schema.columns
   WHERE table_name = 'assets' AND column_name IN ('latitude', 'longitude', 'edge_id');
   ```
   Three rows are expected. If any is missing, add them first:
   ```sql
   ALTER TABLE assets
     ADD COLUMN IF NOT EXISTS latitude  DOUBLE PRECISION,
     ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION,
     ADD COLUMN IF NOT EXISTS edge_id   VARCHAR;
   ```
4. Build the new image as in the usual deploy sequence (README §9), but do not start it yet.
5. Confirm the API's service name first, since the stamp runs in a one-off container of that service:
   ```bash
   sudo docker compose -f /var/www/docker-compose.yml config --services
   ```
6. Stamp from a one-off container of the new image, then start the API. On start-up it runs `alembic upgrade head`, which applies every revision after the baseline.

   | Environment | Service |
   |---|---|
   | Preprod | `rada-app-preprod` |
   | Production | `grid-api` |

   **TODO:** preprod's compose file is assumed to be `/var/www/docker-compose.yml`, like production's. Confirm it (and the service name) with `docker compose config --services` on the VPS, then remove this note.

   Preprod:
   ```bash
   sudo docker compose -f /var/www/docker-compose.yml run --rm rada-app-preprod alembic stamp 0001
   sudo docker compose -f /var/www/docker-compose.yml up -d rada-app-preprod
   sudo docker compose -f /var/www/docker-compose.yml logs rada-app-preprod | grep alembic
   sudo docker compose -f /var/www/docker-compose.yml exec rada-app-preprod alembic current
   ```
   Production:
   ```bash
   sudo docker compose -f /var/www/docker-compose.yml run --rm grid-api alembic stamp 0001
   sudo docker compose -f /var/www/docker-compose.yml up -d grid-api
   sudo docker compose -f /var/www/docker-compose.yml logs grid-api | grep alembic
   sudo docker compose -f /var/www/docker-compose.yml exec grid-api alembic current
   ```
   The last command must show `(head)`.

---

# Migrations de la base de données

*[English above](#database-migrations)*

Le schéma est géré par [Alembic](https://alembic.sqlalchemy.org/). `models.py` et les révisions de `alembic/versions/` décrivent le même schéma, et la CI échoue quand ils divergent. Plus rien d'autre ne crée ni ne modifie de tables : `create_all()`, `create_tables.py` et les fichiers SQL lancés à la main ont disparu.

Alembic lit `DATABASE_URL` (dans l'environnement, ou `.env`) via `database.py`, exactement comme l'API. Le conteneur de l'API lance `alembic upgrade head` avant de démarrer uvicorn.

## Commandes courantes

| Tâche | Commande |
|---|---|
| Appliquer les migrations en attente | `alembic upgrade head` |
| À quelle révision est cette base ? | `alembic current` |
| Lister les révisions | `alembic history` |
| Les modèles et les migrations sont-ils alignés ? | `alembic check` |
| Afficher le SQL sans l'exécuter (pour la relecture) | `alembic upgrade head --sql` |
| Annuler la dernière révision | `alembic downgrade -1` |

## Modifier le schéma

1. Modifier `models.py`.
2. Mettre sa base à `head`, puis générer une révision :
   ```bash
   alembic revision --autogenerate --rev-id 0002 -m "description courte"
   ```
   Passer `--rev-id` avec le numéro suivant. Les fichiers sont nommés `<rev>_<slug>.py` (`file_template` dans `alembic.ini`) : sans cette option, le nom commence par un hachage aléatoire et les révisions ne sont plus listées dans l'ordre.
3. Relire le fichier généré. L'autogénération rate certains changements et en interprète mal d'autres :
   - les migrations de données (remplir ou déplacer des lignes) s'écrivent toujours à la main ;
   - les types enum : `drop_table` les laisse en place, donc `downgrade()` doit les supprimer, et une nouvelle valeur demande `ALTER TYPE … ADD VALUE`. Les enums stockent les **noms** des membres (`'BATTERY'`, pas `'battery'`) ;
   - les objets TimescaleDB (hypertables, agrégats continus) s'écrivent avec `op.execute` ;
   - une colonne ou une table renommée est vue comme une suppression suivie d'un ajout.
4. Écrire un `downgrade()` qui annule réellement `upgrade()`.
5. Lancer `alembic upgrade head`, `alembic downgrade -1`, `alembic upgrade head`, puis `python -m pytest`. Les tests construisent `rada_test` à partir des migrations et incluent l'`alembic check`.
6. Commiter ensemble le changement de modèle et sa révision. Une migration de schéma est relue individuellement avant fusion (`DATA_MODEL.md` §6).

Si deux branches ajoutent chacune une révision sur le même parent, `test_single_head` échoue une fois les deux fusionnées. Rebaser la seconde branche et faire pointer son `down_revision` sur l'autre révision.

## TimescaleDB en local

Un PostgreSQL local n'a pas forcément TimescaleDB. Utiliser le conteneur épinglé sur le port **5434** (choisi pour ne pas entrer en conflit avec un PostgreSQL local sur 5432) :

```bash
docker run -d --name rada-tsdb -p 5434:5432 -e POSTGRES_USER=rada -e POSTGRES_PASSWORD=rada -e POSTGRES_DB=rada -v rada-tsdb-data:/var/lib/postgresql/data timescale/timescaledb:2.32.0-pg16
docker exec rada-tsdb psql -U rada -d postgres -c "create database rada_test"
```

Puis mettre `DATABASE_URL=postgresql://rada:rada@localhost:5434/rada` dans `.env` et lancer `alembic upgrade head`. Les tests utilisent `rada_test` sur le même serveur.

**L'étiquette de l'image est épinglée à la main.** `timescale/timescaledb:2.32.0-pg16` apparaît à deux endroits : `.github/workflows/ci.yml` et la commande ci-dessus. Dependabot ne la met pas à jour (il ne met à jour que les paquets pip et les lignes `uses:` des workflows) : modifier les deux ensemble, en gardant la version majeure de PostgreSQL (`pg16`) identique à celle de la production.

## Montée de version ponctuelle d'une base existante (préprod, production)

Les bases créées avant Alembic ont déjà le schéma de référence (révision `0001`), mais pas de table `alembic_version`. Elles sont **marquées** (`stamp`) à la révision de référence, jamais migrées à travers elle.

> **Marquer la base avant le démarrage de la nouvelle image.** Si le nouveau conteneur de l'API démarre sur une base non marquée, `alembic upgrade head` tente de recréer le schéma de référence et échoue (la première erreur est `type "assettype" already exists`), et l'API ne démarre pas. Rien n'est modifié (la migration tourne dans une seule transaction), mais l'API reste arrêtée tant que la base n'est pas marquée.

1. Sauvegarder la base.
2. Vérifier le fuseau horaire des horodatages existants. La révision 0002 convertit `assets.created_at` et `updated_at` en `timestamptz` en les lisant comme de l'UTC. L'application les écrit en UTC depuis le 31 mai 2026 ; seules des lignes écrites avant, ou hors de l'application, sur un serveur dont le fuseau n'est pas UTC, pourraient être en heure locale :
   ```sql
   SHOW timezone;
   SELECT min(created_at), max(updated_at) FROM assets;
   ```
   Si le serveur n'est pas en UTC et que `min(created_at)` est antérieur au `2026-05-31 16:00`, s'arrêter et vérifier ces lignes avant de continuer. (La préprod et la production ont été rechargées le 17 octobre 2026.)
3. Vérifier que les colonnes de l'ancien `migrations/001_assets_gps_edge.sql` sont présentes. Le marquage enregistre que la base a le schéma de référence : elle doit donc vraiment l'avoir :
   ```sql
   SELECT column_name FROM information_schema.columns
   WHERE table_name = 'assets' AND column_name IN ('latitude', 'longitude', 'edge_id');
   ```
   Trois lignes sont attendues. S'il en manque, les ajouter d'abord :
   ```sql
   ALTER TABLE assets
     ADD COLUMN IF NOT EXISTS latitude  DOUBLE PRECISION,
     ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION,
     ADD COLUMN IF NOT EXISTS edge_id   VARCHAR;
   ```
4. Construire la nouvelle image comme dans la séquence de déploiement habituelle (README §9), sans la démarrer.
5. Confirmer d'abord le nom du service de l'API, car le marquage tourne dans un conteneur ponctuel de ce service :
   ```bash
   sudo docker compose -f /var/www/docker-compose.yml config --services
   ```
6. Marquer la base depuis un conteneur ponctuel de la nouvelle image, puis démarrer l'API. Au démarrage, elle lance `alembic upgrade head`, qui applique toutes les révisions postérieures à la référence.

   | Environnement | Service |
   |---|---|
   | Préprod | `rada-app-preprod` |
   | Production | `grid-api` |

   **À FAIRE :** le fichier compose de la préprod est supposé être `/var/www/docker-compose.yml`, comme celui de la production. Le confirmer (ainsi que le nom du service) avec `docker compose config --services` sur le VPS, puis retirer cette note.

   Préprod :
   ```bash
   sudo docker compose -f /var/www/docker-compose.yml run --rm rada-app-preprod alembic stamp 0001
   sudo docker compose -f /var/www/docker-compose.yml up -d rada-app-preprod
   sudo docker compose -f /var/www/docker-compose.yml logs rada-app-preprod | grep alembic
   sudo docker compose -f /var/www/docker-compose.yml exec rada-app-preprod alembic current
   ```
   Production :
   ```bash
   sudo docker compose -f /var/www/docker-compose.yml run --rm grid-api alembic stamp 0001
   sudo docker compose -f /var/www/docker-compose.yml up -d grid-api
   sudo docker compose -f /var/www/docker-compose.yml logs grid-api | grep alembic
   sudo docker compose -f /var/www/docker-compose.yml exec grid-api alembic current
   ```
   La dernière commande doit afficher `(head)`.
