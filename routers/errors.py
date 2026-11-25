"""
routers/errors.py

A check that needs the database (an unknown metric, an asset on another site)
cannot run in the Pydantic schema, but its 422 should look like one: FastAPI's
list of {type, loc, msg, input}, so a client reads every 422 the same way.
"""

from fastapi.exceptions import RequestValidationError


def field_error(loc: tuple, error_type: str, msg: str, value=None) -> dict:
    """One entry of a 422, shaped like Pydantic's. loc is the path, e.g. ("query", "lang")."""
    return {"type": error_type, "loc": loc, "msg": msg, "input": value}


def unprocessable(errors: list[dict]) -> RequestValidationError:
    """Raise it to answer 422 through FastAPI's own handler: {"detail": [errors]}."""
    return RequestValidationError(errors)
