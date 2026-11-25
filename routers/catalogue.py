"""
routers/catalogue.py

The metric catalogue part of the API (docs/DATA_MODEL.md §8):

- GET /languages: the active languages, the default one flagged.
- GET /metrics: every metric with its names in every active language, or in
  one language with ?lang=, falling back to the default language for a
  metric with no name in it.
"""

from typing import Optional, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from auth.deps import require_read
from database import get_db
from models import Language, Metric, MetricCategory, MetricKind, MetricLevel
from routers.errors import field_error, unprocessable

router = APIRouter(tags=["Metric catalogue"])


# --- Schemas ---

class LanguageOut(BaseModel):
    code: str = Field(..., description="BCP 47 code.", examples=["en"])
    name: str = Field(..., description="The language's own name.", examples=["English"])
    is_default: bool = Field(..., description="Used when a metric has no name in the language asked for.")


class TranslationOut(BaseModel):
    name: str = Field(..., examples=["Battery power"])
    sign_note: Optional[str] = Field(..., examples=["Positive = discharge, negative = charge"])


class MetricFields(BaseModel):
    id: int
    key: str = Field(..., description="Language-neutral key, used to post measurements.", examples=["battery_power"])
    unit: str = Field(..., description="Storage unit: W, Wh, var, %, V, A, °C, or any for a custom metric.",
                      examples=["W"])
    kind: MetricKind
    level: MetricLevel = Field(..., description="site: posted with no asset; asset: needs one; both: either.")
    category: MetricCategory
    is_active: bool = Field(..., description="Measurements of an inactive metric are refused.")


class MetricOut(MetricFields):
    translations: dict[str, TranslationOut] = Field(
        ..., description="Names by language code, in the active languages, default language first.",
    )


class MetricInLanguageOut(MetricFields):
    language: Optional[str] = Field(
        ..., description="The language of name and sign_note: the one asked for, or the default language "
                         "when the metric has no name in it. Null only if it has neither; name is then the key.",
        examples=["fr"],
    )
    name: str = Field(..., examples=["Puissance batterie"])
    sign_note: Optional[str] = Field(..., examples=["Positif = décharge, négatif = charge"])


# --- Helpers ---

def active_languages(db: Session) -> list[Language]:
    """Default language first, then by code."""
    return db.query(Language).filter(Language.is_active).order_by(Language.is_default.desc(), Language.code).all()


def _fields(metric: Metric) -> dict:
    return {name: getattr(metric, name) for name in MetricFields.model_fields}


def metric_out(metric: Metric, codes: list[str]) -> dict:
    """A metric with its names in the given languages, in their order."""
    names = {t.language_code: {"name": t.name, "sign_note": t.sign_note} for t in metric.translations}
    return {**_fields(metric), "translations": {code: names[code] for code in codes if code in names}}


def _metric_in_language(metric: Metric, lang: str, default_code: Optional[str]) -> dict:
    by_code = {t.language_code: t for t in metric.translations}
    translation = by_code.get(lang) or by_code.get(default_code)
    if translation is None:
        return {**_fields(metric), "language": None, "name": metric.key, "sign_note": None}
    return {**_fields(metric), "language": translation.language_code,
            "name": translation.name, "sign_note": translation.sign_note}


# --- Routes ---

@router.get(
    "/languages", response_model=list[LanguageOut],
    dependencies=[Depends(require_read)],
    summary="List the active languages",
    description="The languages metric names can be asked for in (`GET /metrics?lang=`). "
                "The default language comes first.",
)
def list_languages(db: Session = Depends(get_db)):
    return [{"code": lang.code, "name": lang.name, "is_default": lang.is_default} for lang in active_languages(db)]


@router.get(
    "/metrics", response_model=Union[list[MetricOut], list[MetricInLanguageOut]],
    dependencies=[Depends(require_read)],
    summary="List the metric catalogue",
    description="Every metric, inactive ones included, in catalogue order. Without `lang`, each metric has "
                "`translations`: its names in every active language. With `lang`, each metric has `language`, "
                "`name` and `sign_note` instead, in that language or, when the metric has no name in it, "
                "in the default language (`language` says which).",
    responses={422: {"description": "Unknown or inactive language"}},
)
def list_metrics(
    lang: Optional[str] = Query(None, description="An active language code from GET /languages.", examples=["fr"]),
    db: Session = Depends(get_db),
):
    languages = active_languages(db)
    metrics = db.query(Metric).options(selectinload(Metric.translations)).order_by(Metric.id).all()
    if lang is None:
        codes = [language.code for language in languages]
        return [metric_out(metric, codes) for metric in metrics]

    if lang not in {language.code for language in languages}:
        raise unprocessable([field_error(
            ("query", "lang"), "unknown_language", f"Unknown or inactive language {lang!r}: see GET /languages", lang,
        )])
    default_code = db.query(Language.code).filter(Language.is_default).scalar()
    return [_metric_in_language(metric, lang, default_code) for metric in metrics]
