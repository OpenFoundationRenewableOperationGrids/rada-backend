"""
routers/errors.py

A check that needs the database (an unknown metric, an asset on another site)
cannot run in the Pydantic schema, but its 422 should look like one: FastAPI's
list of {type, loc, msg, input}, so a client reads every 422 the same way.

validation_error_handler replaces FastAPI's own 422 handler for the whole app.
It answers the same, except that a NaN or infinite input is echoed as text:
JSON cannot carry it, and FastAPI's handler would answer 500 instead of 422.
"""

import math

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def field_error(loc: tuple, error_type: str, msg: str, value=None) -> dict:
    """One entry of a 422, shaped like Pydantic's. loc is the path, e.g. ("query", "lang")."""
    return {"type": error_type, "loc": loc, "msg": msg, "input": value}


def unprocessable(errors: list[dict]) -> RequestValidationError:
    """Raise it to answer 422 through the app's handler: {"detail": [errors]}."""
    return RequestValidationError(errors)


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # "nan", "inf", "-inf"
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))})
