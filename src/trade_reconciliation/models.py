"""Validated event contracts and immutable domain views."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class EventType(str, Enum):
    NEW = "NEW"
    AMEND = 'AMEND'
    CANCEL = "CANCEL"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


def _bounded_decimal(value: Decimal, field: str) -> Decimal:
    """Prevent hostile exponents/precision from breaking arithmetic or JSON output."""
    if not value.is_finite() or abs(value) > Decimal("1e24"):
        raise ValueError(f"{field} must be finite and at most 1e24 in magnitude")
    if value.as_tuple().exponent < -18:
        raise ValueError(f"{field} must have at most 18 decimal places")
    return value


class TradeEvent(BaseModel):
    """A complete trade version, not a partial patch."""

    model_config = ConfigDict(frozen=True, extra='ignore')

    event_id: str
    trade_id: str
    version: int
    type: EventType
    account: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    venue: str
    event_time: datetime


    @field_validator("event_id", "trade_id", "account", "symbol", "venue", mode="before")
    @classmethod
    def validate_identifier(cls, value: object) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("identifier must be a non-empty string without surrounding whitespace")
        if len(value) > 256 or any(ord(ch) < 32 or ch == '�' for ch in value):
            raise ValueError("identifier contains invalid characters or exceeds 256 characters")
        return value


    @field_validator('version', mode='before')
    @classmethod
    def validate_version(cls, value: object) -> int:
        if type(value) is not int or value < 1:
            raise ValueError('version must be a positive integer')
        return value

    @field_validator('quantity', mode="before")
    @classmethod
    def validate_quantity(cls, value: object) -> Decimal:
        if type(value) is int or isinstance(value, Decimal):
            result = Decimal(value)
            if result.is_finite():
                return _bounded_decimal(result, "quantity")
        raise ValueError('quantity must be a finite JSON number')


    @field_validator('price', mode="before")
    @classmethod
    def validate_price(cls, value: object) -> Decimal:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("price must be a decimal string")
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("price must be a decimal string") from exc
        return _bounded_decimal(result, "price")

    @field_validator("event_time", mode='before')
    @classmethod
    def require_iso_timestamp(cls, value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", value
        ):
            raise ValueError('event_time must be an ISO 8601 UTC string')
        return value


    @field_validator('event_time')
    @classmethod
    def validate_utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError('event_time must specify UTC')
        return value.astimezone(timezone.utc)

    @model_validator(mode='after')
    def validate_values_for_type(self) -> TradeEvent:
        minimum = Decimal(0)
        if self.type is EventType.CANCEL:
            if self.quantity < minimum or self.price < minimum:
                raise ValueError('CANCEL requires non-negative quantity and price')
        elif self.quantity <= minimum or self.price <= minimum:
            raise ValueError("NEW and AMEND require positive quantity and price")
        return self


    def business_signature(self) -> tuple[object, ...]:
        """Exclude event identity and timestamp from a version's business identity."""
        return (
            self.trade_id, self.version, self.type, self.account,
            self.symbol, self.side, self.quantity, self.price, self.venue,
        )


    def contribution(self) -> tuple[tuple[str, str], Decimal] | None:
        if self.type is EventType.CANCEL:
            return None
        signed = self.quantity if self.side is Side.BUY else -self.quantity
        return (self.account, self.symbol), signed

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "trade_id": self.trade_id,
            "version": self.version,
            "type": self.type.value,
            "account": self.account,
            "symbol": self.symbol,
            'side': self.side.value,
            'quantity': self.quantity,
            'price': str(self.price),
            "venue": self.venue,
            "event_time": self.event_time.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class VersionView:
    version: int
    events: tuple[TradeEvent, ...]
    conflicted: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "conflicted": self.conflicted,
            'events': [e.to_dict() for e in self.events],
        }


@dataclass(frozen=True)
class TradeView:
    trade_id: str
    status: str  # ACTIVE / CANCELLED / CONFLICTED
    highest_version: int | None
    current: TradeEvent | None
    history: tuple[VersionView, ...]
    conflicting_versions: tuple[int, ...]
    corrupted_event_ids: tuple[str, ...]
    anomalies: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "trade_id": self.trade_id,
            'status': self.status,
            'highest_version': self.highest_version,
            'current': self.current.to_dict() if self.current else None,
            "history": [v.to_dict() for v in self.history],
            "has_conflicts": bool(self.conflicting_versions or self.corrupted_event_ids),
            "conflicting_versions": list(self.conflicting_versions),
            'corrupted_event_ids': list(self.corrupted_event_ids),
            "anomalies": list(self.anomalies),
        }
