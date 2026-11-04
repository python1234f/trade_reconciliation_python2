"""Randomized adversarial order-independence checks (runs with dev extras)."""

import pytest

hypothesis = pytest.importorskip('hypothesis')
from hypothesis import given, settings, strategies as st

from trade_reconciliation.engine import TradeEngine
from trade_reconciliation.ingestion import parse_event
from trade_reconciliation.jsoncodec import dumps_exact


def _result(events):
    engine = TradeEngine()
    for event in events:
        engine.apply(event)
    snap = engine.snapshot()
    return (
        snap.positions,
        snap.uncertain,
        tuple((t.trade_id, t.status, t.highest_version, t.conflicting_versions,
               t.corrupted_event_ids,
               tuple((v.version, v.conflicted, tuple(e.event_id for e in v.events)) for v in t.history))
              for t in snap.trades),
    )


@given(st.lists(st.tuples(
    st.integers(min_value=0, max_value=6),
    st.integers(min_value=0, max_value=5),
    st.integers(min_value=1, max_value=7),
    st.sampled_from(["NEW", 'AMEND', "CANCEL"]),
    st.integers(min_value=0, max_value=100),
), min_size=0, max_size=25))
@settings(max_examples=100, deadline=None)
def test_valid_event_multisets_are_order_independent(specs):
    events = []
    for event_id, trade_id, version, kind, n in specs:
        record = {
            'event_id': f"e{event_id}", "trade_id": f"T{trade_id}",
            'version': version, 'type': kind,
            "account": f"A{n % 4}", "symbol": "AAPL" if n % 2 else "MSFT",
            'side': "BUY" if n % 3 else "SELL",
            "quantity": n or (0 if kind == "CANCEL" else 1),
            "price": "0" if kind == 'CANCEL' else "10.00",
            "venue": 'XNAS', "event_time": '2026-11-02T14:30:01Z',
        }
        events.append(parse_event(dumps_exact(record)))
    assert _result(events) == _result(list(reversed(events)))
