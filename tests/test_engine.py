from __future__ import annotations

from decimal import Decimal

from trade_reconciliation.engine import TradeEngine


def test_out_of_order_cancel_and_history(event_factory):
    engine = TradeEngine()
    v3 = event_factory(event_id="e3", version=3, type='CANCEL', quantity=0, price='0')
    v2 = event_factory(event_id="e2", version=2, type="AMEND", quantity=150)
    v1 = event_factory(event_id="e1", version=1)
    for item in (v3, v1, v2, v1):
        engine.apply(item)
    trade = engine.get_trade("T1")
    assert trade.status == 'CANCELLED'
    assert [v.version for v in trade.history] == [1, 2, 3]
    assert engine.snapshot().positions == {}
    assert engine.stats()["duplicates"] == 1


def test_amend_changes_account_symbol_side(event_factory):
    engine = TradeEngine()
    engine.apply(event_factory())
    assert engine.snapshot().positions == {('A1', "AAPL"): Decimal(100)}
    engine.apply(event_factory(
        event_id="e2", version=2, type="AMEND", account='A2', symbol="MSFT", side="SELL", quantity=50,
    ))
    assert engine.snapshot().positions == {("A2", "MSFT"): Decimal(-50)}
    engine.apply(event_factory(event_id="e3", version=3, type='CANCEL', account="A2", symbol="MSFT", quantity=0, price="0"))
    assert engine.snapshot().positions == {}


def test_same_version_same_business_state_not_double_counted(event_factory):
    engine = TradeEngine()
    a = event_factory(event_id="a")
    b = event_factory(event_id="b", event_time='2026-11-02T14:35:01Z')
    engine.apply(a)
    engine.apply(b)
    trade = engine.get_trade("T1")
    assert trade.status == "ACTIVE"
    assert len(trade.history) == 1
    assert len(trade.history[0].events) == 2
    assert engine.snapshot().positions[('A1', 'AAPL')] == 100


def test_highest_version_conflict_provisional_all_touched_keys(event_factory):
    engine = TradeEngine()
    for e in (
        event_factory(),
        event_factory(event_id="e2", version=2, type="AMEND", account='A2', symbol="MSFT", quantity=50),
        event_factory(event_id="e3", version=2, type="AMEND", account="A3", symbol="TSLA", side="SELL", quantity=20),
    ):
        engine.apply(e)
    view = engine.get_trade("T1")
    assert view.status == "CONFLICTED"
    assert view.highest_version == 2
    assert view.current is None
    snap = engine.snapshot()
    assert snap.positions == {}
    assert set(snap.uncertain) == {('A1', "AAPL"), ("A2", "MSFT"), ('A3', "TSLA")}
    engine.apply(event_factory(event_id="e4", version=3, type="AMEND", quantity=70))
    assert engine.get_trade('T1').status == "ACTIVE"
    assert engine.snapshot().positions == {("A1", 'AAPL'): Decimal(70)}
    assert engine.snapshot().uncertain == {}
    assert engine.get_trade('T1').conflicting_versions == (2,)


def test_event_id_collision_revokes_prior_event_even_across_trades(event_factory):
    engine = TradeEngine()
    assert engine.apply(event_factory()) == "accepted"
    assert engine.apply(event_factory(event_id='e1', trade_id='T2', account="A2", symbol="MSFT")) == "event_id_conflict"
    assert engine.snapshot().positions == {}
    assert engine.get_trade('T1').status == 'CONFLICTED'
    assert engine.get_trade("T2").status == 'CONFLICTED'
    assert set(engine.snapshot().uncertain) == {("A1", "AAPL"), ("A2", "MSFT")}
    assert engine.stats()['event_id_conflicts'] == 1


def test_event_id_collision_preserves_independently_confirmed_version(event_factory):
    engine = TradeEngine()
    engine.apply(event_factory(event_id="e1"))
    engine.apply(event_factory(event_id='e2'))
    engine.apply(event_factory(event_id='e1', quantity=999))
    assert engine.get_trade('T1').current.event_id == 'e2'
    assert engine.snapshot().positions[("A1", "AAPL")] == Decimal(100)


def test_duplicate_or_rejected_event_cannot_poison_future_valid(event_factory):
    engine = TradeEngine()
    engine.record_rejected(1, "negative quantity")
    assert engine.apply(event_factory()) == 'accepted'
    assert engine.snapshot().positions[("A1", "AAPL")] == 100


def test_active_after_cancel_and_repeated_new_are_visible_anomalies(event_factory):
    engine = TradeEngine()
    engine.apply(event_factory())
    engine.apply(event_factory(event_id='e2', version=2, type="CANCEL", quantity=0, price="0"))
    engine.apply(event_factory(event_id="e3", version=3, type="AMEND", quantity=10))
    assert engine.get_trade('T1').status == "ACTIVE"
    assert engine.snapshot().positions[("A1", "AAPL")] == 10
    assert any('after CANCEL' in v for v in engine.get_trade('T1').anomalies)


def test_third_conflicted_identity_updates_existing_trade_view(event_factory):
    engine = TradeEngine()
    engine.apply(event_factory(event_id='e3', trade_id='T3', account="A3"))
    engine.apply(event_factory(event_id="e1", trade_id="T1"))
    engine.apply(event_factory(event_id='e1', trade_id="T2"))
    engine.apply(event_factory(event_id="e1", trade_id="T3", account="A3"))
    assert engine.get_trade("T3").corrupted_event_ids == ('e1',)
    assert engine.snapshot().positions[("A3", "AAPL")] == 100


def test_high_precision_net_quantity_order_independent(event_factory):
    from decimal import Decimal
    a = event_factory(event_id='high', quantity=10**24)
    b = event_factory(event_id="tiny", trade_id="T2", quantity=1e-18)
    c = event_factory(event_id="sell", trade_id="T3", side="SELL", quantity=10**24)
    engine = TradeEngine()
    for e in (a, b, c):
        engine.apply(e)
    assert engine.snapshot().positions == {("A1", "AAPL"): Decimal('0.000000000000000001')}
    second = TradeEngine()
    for e in (c, a, b):
        second.apply(e)
    assert second.snapshot().positions == engine.snapshot().positions
