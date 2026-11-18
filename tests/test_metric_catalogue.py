"""
test_metric_catalogue.py

seed_metrics.py, the loader of seeds/metrics.toml: it loads the shipped
catalogue, a second run changes nothing, it updates names, keeps an
administrator's default language, and refuses what needs a migration.
"""

import pytest

import seed_metrics
from models import Language, Metric, MetricCategory, MetricKind, MetricLevel, MetricTranslation
from seed_metrics import CatalogueError, load_metric_catalogue

SHIPPED = seed_metrics.CATALOGUE.read_text(encoding="utf-8")


def catalogue_file(tmp_path, text):
    path = tmp_path / "metrics.toml"
    path.write_text(text, encoding="utf-8")
    return path


def battery_soc_with(field, old, new):
    """The shipped catalogue with one field of battery_soc changed."""
    block_start = SHIPPED.index('key = "battery_soc"')
    block_end = SHIPPED.index("[[metric]]", block_start)
    block = SHIPPED[block_start:block_end]
    assert f'{field} = "{old}"' in block
    return SHIPPED[:block_start] + block.replace(f'{field} = "{old}"', f'{field} = "{new}"') + SHIPPED[block_end:]


def row_versions(db):
    """Every catalogue row with its updated_at, to prove a run wrote nothing."""
    return (
        sorted((m.key, m.updated_at) for m in db.query(Metric)),
        sorted((t.metric_id, t.language_code, t.updated_at) for t in db.query(MetricTranslation)),
        sorted((language.code, language.updated_at) for language in db.query(Language)),
    )


@pytest.fixture()
def loaded(db_session):
    counts = load_metric_catalogue(db_session)
    db_session.commit()
    return counts


def test_loads_the_shipped_catalogue(db_session, loaded):
    assert loaded == {"added": 3 + 16 + 48, "updated": 0}
    assert db_session.query(Language).filter(Language.is_default).one().code == "en"
    grid = db_session.query(Metric).filter(Metric.key == "grid_power").one()
    assert (grid.unit, grid.kind, grid.level, grid.category) == (
        "W", MetricKind.INSTANTANEOUS, MetricLevel.BOTH, MetricCategory.CORE)
    names = {t.language_code: t.name for t in grid.translations}
    assert names == {"en": "Grid power", "fr": "Puissance réseau", "de": "Netzleistung"}


def test_loading_twice_changes_nothing(db_session, loaded):
    before = row_versions(db_session)

    assert load_metric_catalogue(db_session) == {"added": 0, "updated": 0}
    db_session.commit()
    db_session.expire_all()
    assert row_versions(db_session) == before


def test_updates_a_translated_name(db_session, loaded, tmp_path):
    renamed = SHIPPED.replace('fr = "Puissance réseau"', 'fr = "Puissance au point de livraison"')

    counts = load_metric_catalogue(db_session, catalogue_file(tmp_path, renamed))
    db_session.commit()

    assert counts == {"added": 0, "updated": 1}
    grid = db_session.query(Metric).filter(Metric.key == "grid_power").one()
    assert {t.language_code: t.name for t in grid.translations}["fr"] == "Puissance au point de livraison"


def test_keeps_an_administrators_default_language(db_session, loaded):
    db_session.query(Language).filter(Language.code == "en").one().is_default = False
    db_session.flush()
    db_session.query(Language).filter(Language.code == "fr").one().is_default = True
    db_session.commit()

    load_metric_catalogue(db_session)
    db_session.commit()

    assert db_session.query(Language).filter(Language.is_default).one().code == "fr"


@pytest.mark.parametrize("field, old, new", [
    ("unit", "%", "ratio"),
    ("kind", "state", "instantaneous"),
    ("level", "both", "asset"),
])
def test_refuses_to_change_unit_kind_or_level(db_session, loaded, tmp_path, field, old, new):
    before = row_versions(db_session)

    with pytest.raises(CatalogueError, match=rf"Metric battery_soc: .*{field} from '{old}' to '{new}'.*needs a migration"):
        load_metric_catalogue(db_session, catalogue_file(tmp_path, battery_soc_with(field, old, new)))
    db_session.rollback()

    assert row_versions(db_session) == before


def test_refuses_a_key_used_by_a_custom_metric(db_session, loaded, tmp_path):
    db_session.add(Metric(key="site_temperature", unit="°C", kind=MetricKind.INSTANTANEOUS,
                          level=MetricLevel.SITE, category=MetricCategory.CUSTOM))
    db_session.commit()
    extra = SHIPPED + (
        '\n[[metric]]\nkey = "site_temperature"\nunit = "°C"\nkind = "instantaneous"\n'
        'level = "site"\ncategory = "optional"\nname = { en = "Site temperature" }\n'
    )

    with pytest.raises(CatalogueError, match="site_temperature: the key is already used by a custom metric"):
        load_metric_catalogue(db_session, catalogue_file(tmp_path, extra))
    db_session.rollback()


@pytest.mark.parametrize("change, message", [
    (lambda text: text.replace("is_default = true", "is_default = false"), "Exactly one language"),
    (lambda text: text.replace('category = "core"', 'category = "custom"', 1), "custom metrics are created through POST /metrics"),
    (lambda text: text.replace('name = { en = "Production power", ', "name = { ", 1), "no name in the default language"),
    (lambda text: text.replace('kind = "instantaneous"', 'kind = "instant"', 1), "'instant' is not a valid MetricKind"),
])
def test_refuses_an_invalid_file(db_session, tmp_path, change, message):
    with pytest.raises(CatalogueError, match=message):
        load_metric_catalogue(db_session, catalogue_file(tmp_path, change(SHIPPED)))
    db_session.rollback()

    assert db_session.query(Metric).count() == 0  # nothing half-loaded


def test_command_line_exits_non_zero_on_error(db_session, loaded, tmp_path, monkeypatch, capsys):
    # The container runs `python seed_metrics.py && exec uvicorn`: 1 keeps the API down
    monkeypatch.setattr(seed_metrics, "CATALOGUE", catalogue_file(tmp_path, battery_soc_with("unit", "%", "ratio")))

    assert seed_metrics.main() == 1
    assert "Metric battery_soc" in capsys.readouterr().err


def test_command_line_succeeds_on_the_shipped_catalogue(capsys):
    assert seed_metrics.main() == 0
    assert "Metric catalogue loaded: 67 rows added, 0 updated." in capsys.readouterr().out
