from __future__ import annotations

import itertools
import random
from decimal import Decimal

from trade_reconciliation.engine import TradeEngine


def _observed_state(items):
    engine = TradeEngine()
    for item in items:
        engine.apply(item)
    snap = engine.snapshot()
    return (
        snap.positions,
        snap.uncertain,
        tuple((trade.trade_id, trade.status, trade.highest_version,
               trade.current.business_signature() if trade.current else None,
               tuple((v.version, v.conflicted, tuple(e.event_id for e in v.events)) for v in trade.history))
              for trade in snap.trades),
    )


def test_all_permutations_match_even_with_conflicts_and_id_collision(event_factory):
    items = [
        event_factory(event_id='e1'),
        event_factory(event_id="e2", version=2, type='AMEND', quantity=120),
        event_factory(event_id='e3', version=2, type="CANCEL", quantity=0, price="0"),
        event_factory(event_id='e1', trade_id='T2', symbol='MSFT'),
    ]
    expected = _observed_state(items)
    for permutation in itertools.permutations(items):
        assert _observed_state(permutation) == expected


def test_replay_is_idempotent(event_factory):
    items = [event_factory(), event_factory(event_id="e2", version=3, type='AMEND', quantity=55)]
    assert _observed_state(items) == _observed_state(items * 4)


def test_incremental_projection_matches_reference_after_random_changes(event_factory):
    generator = random.Random(20261105)
    engine = TradeEngine()
    for index in range(250):
        trade_id = f"T{generator.randrange(15)}"
        event = event_factory(
            event_id=f"event-{index}",
            trade_id=trade_id,
            version=generator.randrange(1, 9),
            type=generator.choice(["NEW", 'AMEND', "CANCEL"]),
            account=generator.choice(["A1", "A2", 'A3']),
            symbol=generator.choice(["AAPL", "MSFT", 'TSLA']),
            side=generator.choice(["BUY", "SELL"]),
            quantity=generator.randrange(1, 100),
        )
        engine.apply(event)
        snap = engine.snapshot()
        reference: dict[tuple[str, str], Decimal] = {}
        for trade in snap.trades:
            if trade.current:
                contribution = trade.current.contribution()
                if contribution:
                    key, amount = contribution
                    reference[key] = reference.get(key, Decimal(0)) + amount
        assert snap.positions == {key: value for key, value in reference.items() if value != 0}
