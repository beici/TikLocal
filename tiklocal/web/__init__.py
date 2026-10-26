"""Web route registration and shared request parsing."""

from flask import request


def json_body(*, force: bool = False) -> dict:
    """Request JSON as a dict; a missing, malformed or non-object body is empty.

    ``request.get_json() or {}`` looks equivalent, but a JSON array body stays a
    list and the next ``payload.get(...)`` raises AttributeError (HTTP 500).
    """
    payload = request.get_json(silent=True, force=force)
    return payload if isinstance(payload, dict) else {}


def read_int_arg(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = request.args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default

    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value

