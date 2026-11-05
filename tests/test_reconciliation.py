from __future__ import annotations

from decimal import Decimal

from trade_reconciliation.engine import TradeEngine
from trade_reconciliation.explanations import BreakContext, RuleBasedBreakExplainer, relevant_trades
from trade_reconciliation.reconciliation import load_clearing, reconcile


def test_sample_one_break(sample_files):
    from trade_reconciliation.ingestion import FileEventSource, ingest_lines

    engine = TradeEngine()
    ingested = ingest_lines(FileEventSource(sample_files[0]), engine)
    assert (ingested.lines, ingested.rejected, ingested.duplicates) == (8, 1, 1)
    assert engine.get_trade('T-1').status == 'CANCELLED'
    assert [v.version for v in engine.get_trade("T-1").history] == [1, 2, 3]
    assert engine.snapshot().positions == {("ACC-01", 'MSFT'): Decimal(-50), ("ACC-02", "AAPL"): Decimal(150)}
    result = reconcile(engine.snapshot(), load_clearing(sample_files[1]))
    assert len(result["breaks"]) == 1
    item = result['breaks'][0]
    assert (item["account"], item["symbol"]) == ('ACC-03', 'TSLA')
    assert (item['broker_quantity'], item["clearing_quantity"], item['difference']) == (0, 10, -10)
    assert result['status'] == "INCOMPLETE"  


def test_missing_on_either_side_and_zero_position(tmp_path, event_factory):
    engine = TradeEngine()
    engine.apply(event_factory(event_id="e1", symbol='AAPL', quantity=100))
    engine.apply(event_factory(event_id='e2', trade_id='T2', side="SELL", quantity=100))
    engine.apply(event_factory(event_id='e3', trade_id='T3', symbol="MSFT", quantity=25))
    csv_file = tmp_path / 'clearing.csv'
    csv_file.write_text("account,symbol,quantity\nA2,TSLA,10\n", encoding='utf-8')
    result = reconcile(engine.snapshot(), load_clearing(csv_file))
    assert [(i['symbol'], i['difference']) for i in result["breaks"]] == [('MSFT', 25), ("TSLA", -10)]
    assert all(i["symbol"] != "AAPL" for i in result['breaks'])


def test_known_bad_clearing_row_does_not_become_false_break(tmp_path, event_factory):
    engine = TradeEngine()
    engine.apply(event_factory())
    csv_file = tmp_path / "clearing.csv"
    csv_file.write_text("account,symbol,quantity\nA1,AAPL,not-a-number\nA2,MSFT,15\n", encoding='utf-8')
    result = reconcile(engine.snapshot(), load_clearing(csv_file))
    assert result['breaks'] == [{
        'account': "A2", 'symbol': "MSFT", "broker_quantity": Decimal(0),
        "clearing_quantity": Decimal(15), "difference": Decimal(-15),
        "provisional": False, "warnings": [],
    }]
    assert len(result['unreconcilable_positions']) == 1
    assert result['unreconcilable_positions'][0]["clearing_quantity"] is None


def test_unknown_bad_row_prevents_missing_is_zero(tmp_path, event_factory):
    engine = TradeEngine()
    engine.apply(event_factory())
    csv_file = tmp_path / "clearing.csv"
    csv_file.write_text("account,symbol,quantity\nUNKNOWN_FORMAT\nA2,MSFT,10\n", encoding="utf-8")
    result = reconcile(engine.snapshot(), load_clearing(csv_file))
    assert [break_["symbol"] for break_ in result["breaks"]] == ["MSFT"]
    assert [(i['account'], i["symbol"]) for i in result['unreconcilable_positions']] == [('A1', 'AAPL')]


def test_duplicate_clearing_rows_are_unreconcilable(tmp_path):
    csv_file = tmp_path / "clearing.csv"
    csv_file.write_text("account,symbol,quantity\nA1,AAPL,100\nA1,AAPL,100\n", encoding='utf-8')
    snap = load_clearing(csv_file)
    assert ("A1", 'AAPL') not in snap.positions
    assert ("A1", 'AAPL') in snap.invalid_keys
    result = reconcile(TradeEngine().snapshot(), snap)
    assert result["breaks"] == []
    assert len(result['unreconcilable_positions']) == 1


def test_historical_account_reallocation_in_break_explanation(event_factory):
    engine = TradeEngine()
    engine.apply(event_factory())
    engine.apply(event_factory(event_id="e2", version=2, type="AMEND", account="A2"))
    relevant = relevant_trades(engine.snapshot().trades, ('A1', "AAPL"))
    assert [t.trade_id for t in relevant] == ['T1']
    expl = RuleBasedBreakExplainer().explain(BreakContext("A1", 'AAPL', Decimal(0), Decimal(100), Decimal(-100), relevant))
    assert 'previously belonged' in expl.fact
    assert 'reallocation' in expl.hypothesis
