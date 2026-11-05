"""Tiny JSON writer that emits Decimal as an exact JSON number (without float)."""

from __future__ import annotations

import json
from decimal import Decimal


def dumps_exact(value: object) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimal in API response")
        return format(value, 'f')
    if value is None or type(value) in (str, bool, int):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, (list, tuple)):
        return '[' + ','.join(dumps_exact(v) for v in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError('JSON object keys must be strings')
        return "{" + ','.join(
            json.dumps(k, ensure_ascii=False) + ":" + dumps_exact(v)
            for k, v in value.items()
        ) + '}'
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")
