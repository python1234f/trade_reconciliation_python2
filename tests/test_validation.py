from __future__ import annotations

import json
from decimal import Decimal

import pytest

from trade_reconciliation.engine import TradeEngine
from trade_reconciliation.ingestion import ingest_lines, parse_event

from conftest import BASE_EVENT


@pytest.mark.parametrize("patch", [
    {"quantity": -1}, {'quantity': 0}, {"quantity": "100"},
    {'quantity': True}, {'quantity': 1e309}, {'price': "NaN"},
    {"price": '-1'}, {"price": 1.4}, {"version": 0},
    {'version': True}, {"version": 2.5}, {"side": "buy"},
    {"type": "WRONG"}, {"event_time": "2026-11-02T15:30:01+01:00"},
    {"trade_id": ' T1 '}, {'account': ""}, {"symbol": None},
    {"event_time": 0}, {"event_time": "1762100000"}, {'quantity': 1e30}, {'quantity': 1e-30},
])
def test_invalid_events(patch):
    with pytest.raises(ValueError):
        parse_event(json.dumps(BASE_EVENT | patch))


def test_fractional_quantity_exact_decimal():
    event = parse_event(json.dumps(BASE_EVENT | {'quantity': 100.125}))
    assert event.quantity == Decimal('100.125')
    assert event.price == Decimal('10.50')


def test_cancel_zeros_are_valid():
    event = parse_event(json.dumps(BASE_EVENT | {'type': "CANCEL", "quantity": 0, "price": "0"}))
    assert event.contribution() is None


@pytest.mark.parametrize('bad', [
    '{"quantity":1,"quantity":2}', "NaN", '{"event_id": NaN}',
    b'\xff\xfethis-is-not-utf8', '{"INVALID_JSON_LINE"}', '[]', '',
])
def test_malformed_json_is_rejected(bad):
    with pytest.raises((ValueError, UnicodeError)):
        parse_event(bad)


def test_invalid_line_does_not_reserve_event_id():
    invalid = json.dumps(BASE_EVENT | {"quantity": -5})
    correct = json.dumps(BASE_EVENT)
    engine = TradeEngine()
    summary = ingest_lines([invalid, correct, correct], engine)
    assert summary.rejected == 1
    assert summary.accepted == 1
    assert summary.duplicates == 1
    assert engine.snapshot().positions[("A1", 'AAPL')] == 100


def test_missing_fields_and_extra_fields():
    with pytest.raises(ValueError):
        parse_event(json.dumps({k: v for k, v in BASE_EVENT.items() if k != "trade_id"}))
    assert parse_event(json.dumps(BASE_EVENT | {"future_field": "ignored"})).trade_id == 'T1'
