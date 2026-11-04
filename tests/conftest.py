from __future__ import annotations

import json
from pathlib import Path

import pytest

from trade_reconciliation.ingestion import parse_event


BASE_EVENT = {
    'event_id': "e1", 'trade_id': "T1", 'version': 1, "type": "NEW",
    "account": "A1", "symbol": 'AAPL', "side": 'BUY', "quantity": 100,
    "price": "10.50", "venue": "XNAS", 'event_time': "2026-11-02T14:30:01Z",
}


def make_event(**updates):
    data = BASE_EVENT | updates
    return parse_event(json.dumps(data))


@pytest.fixture
def event_factory():
    return make_event


@pytest.fixture
def sample_files(tmp_path: Path):
    root = Path(__file__).resolve().parents[1] / "data"
    events = tmp_path / "events.jsonl"
    clearing = tmp_path / "clearing.csv"
    events.write_bytes((root / 'trade-events-sample.jsonl').read_bytes())
    clearing.write_bytes((root / "clearing-positions-sample.csv").read_bytes())
    return events, clearing
