"""
seed_metrics.py

Loads the metric catalogue of seeds/metrics.toml (docs/DATA_MODEL.md §3) into
language, metric and metric_translation. Runs on every container start, after
`alembic upgrade head`, and from seed_batteries.py. Safe to run any number of
times: a run with nothing new changes no row.

What it may do:
- add languages, metrics and translations;
- update a language's name, a metric's category, and translated names and
  sign notes;
- set the default language, but only while the install has none, so a
  default chosen later by an administrator survives restarts.

What it refuses, with an error naming the metric, leaving the database as it was:
- changing a metric's unit, kind or level: rows were stored and mapped under
  the old value, so that change needs a migration;
- a key already used by a custom metric. Custom metrics are created through
  POST /metrics, never here.

It never deletes anything and never touches is_active.

Usage:
    python seed_metrics.py
"""

import sys
import tomllib
from pathlib import Path

from database import SessionLocal
from models import Language, Metric, MetricCategory, MetricKind, MetricLevel, MetricTranslation

CATALOGUE = Path(__file__).parent / "seeds" / "metrics.toml"

# Changing one of these on an existing metric needs a migration
FIXED_FIELDS = ("unit", "kind", "level")


class CatalogueError(Exception):
    """The catalogue file is invalid, or would change what only a migration may change."""


def _shown(value):
    return getattr(value, "value", value)


def _update(row, values, counts):
    """Set only the fields that differ, so an unchanged row is never written."""
    changed = False
    for field, value in values.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed = True
    if changed:
        counts["updated"] += 1


def _load_languages(db, entries, counts):
    """Add or update the languages; return the codes the catalogue may use."""
    defaults = [entry["code"] for entry in entries if entry.get("is_default")]
    if len(defaults) != 1:
        raise CatalogueError(f"Exactly one language must have is_default = true, found {defaults or 'none'}")

    existing = {language.code: language for language in db.query(Language)}
    has_default = any(language.is_default for language in existing.values())
    for entry in entries:
        language = existing.get(entry["code"])
        if language is None:
            language = Language(code=entry["code"], name=entry["name"], is_default=False)
            db.add(language)
            existing[entry["code"]] = language
            counts["added"] += 1
        else:
            _update(language, {"name": entry["name"]}, counts)
    if not has_default:
        existing[defaults[0]].is_default = True
    db.flush()
    return set(existing), next(code for code, language in existing.items() if language.is_default)


def _load_metric(db, entry, codes, default_code, counts):
    key = entry.get("key", "?")
    try:
        values = {
            "unit": entry["unit"],
            "kind": MetricKind(entry["kind"]),
            "level": MetricLevel(entry["level"]),
            "category": MetricCategory(entry["category"]),
        }
    except KeyError as error:
        raise CatalogueError(f"Metric {key}: missing field {error}") from None
    except ValueError as error:
        raise CatalogueError(f"Metric {key}: {error}") from None
    if values["category"] is MetricCategory.CUSTOM:
        raise CatalogueError(f"Metric {key}: custom metrics are created through POST /metrics, not the catalogue file")

    names = entry.get("name", {})
    sign_notes = entry.get("sign_note", {})
    if default_code not in names:
        raise CatalogueError(f"Metric {key}: no name in the default language ({default_code})")
    unknown = sorted(set(names) - codes)
    if unknown:
        raise CatalogueError(f"Metric {key}: names in unknown languages {unknown}")

    metric = db.query(Metric).filter(Metric.key == key).one_or_none()
    if metric is None:
        metric = Metric(key=key, **values)
        db.add(metric)
        counts["added"] += 1
    else:
        if metric.category is MetricCategory.CUSTOM:
            raise CatalogueError(f"Metric {key}: the key is already used by a custom metric")
        changes = [
            f"{field} from {_shown(getattr(metric, field))!r} to {_shown(values[field])!r}"
            for field in FIXED_FIELDS if getattr(metric, field) != values[field]
        ]
        if changes:
            raise CatalogueError(
                f"Metric {key}: the catalogue changes its {', '.join(changes)}. "
                "Rows were stored under the old value, so this change needs a migration."
            )
        _update(metric, {"category": values["category"]}, counts)

    translations = {translation.language_code: translation for translation in metric.translations}
    for code, name in names.items():
        translation_values = {"name": name, "sign_note": sign_notes.get(code)}
        translation = translations.get(code)
        if translation is None:
            metric.translations.append(MetricTranslation(language_code=code, **translation_values))
            counts["added"] += 1
        else:
            _update(translation, translation_values, counts)


def load_metric_catalogue(db, path=None):
    """Add or update the catalogue in db (from CATALOGUE unless a path is given), without committing.

    Returns {"added": n, "updated": n}, counted in rows. Raises CatalogueError
    before anything is committed, so the caller can roll back.
    """
    with open(CATALOGUE if path is None else path, "rb") as file:
        catalogue = tomllib.load(file)
    counts = {"added": 0, "updated": 0}
    codes, default_code = _load_languages(db, catalogue.get("language", []), counts)
    for entry in catalogue.get("metric", []):
        _load_metric(db, entry, codes, default_code, counts)
    db.flush()
    return counts


def main():
    db = SessionLocal()
    try:
        counts = load_metric_catalogue(db)
        db.commit()
    except CatalogueError as error:
        db.rollback()
        print(f"❌ Metric catalogue not loaded: {error}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"Metric catalogue loaded: {counts['added']} rows added, {counts['updated']} updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
