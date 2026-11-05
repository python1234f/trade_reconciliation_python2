from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from trade_reconciliation.api import create_app
from trade_reconciliation.explanations import OpenAIBreakExplainer
from trade_reconciliation.jsoncodec import dumps_exact


def test_api_with_sample(sample_files):
    app = create_app(*sample_files)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["ingestion"]["rejected"] == 1
        positions = client.get('/positions/ACC-01').json()['positions']
        assert positions == [{'symbol': 'MSFT', "quantity": -50, "provisional": False, "warnings": []}]
        assert client.get("/positions/UNKNOWN").json()['positions'] == []
        trade = client.get("/trades/T-1").json()
        assert trade['status'] == "CANCELLED"
        assert [v["version"] for v in trade['history']] == [1, 2, 3]
        assert client.get("/trades/missing").status_code == 404
        report = client.get("/reconciliation").json()
        assert len(report['breaks']) == 1
        assert report["breaks"][0]["difference"] == -10
        assert set(report['breaks'][0]["explanation"]) == {"fact", 'hypothesis', 'suggested_action', 'source'}
        assert 'explanation' not in client.get("/reconciliation?include_explanations=false").json()["breaks"][0]


def test_exact_decimal_serialization():
    encoded = dumps_exact({"x": Decimal("100.50000000000000000000000001"), "y": Decimal("10.00")})
    assert encoded == '{"x":100.50000000000000000000000001,"y":10.00}'


def test_openai_adapter_fallback_without_network():
    

    class FaultyClient:
        class responses:
            @staticmethod
            def create(**kwargs):
                raise TimeoutError("provider unavailable")

    adapter = OpenAIBreakExplainer.__new__(OpenAIBreakExplainer)
    adapter.client = FaultyClient()
    adapter.model = 'unused'
    from trade_reconciliation.explanations import BreakContext, RuleBasedBreakExplainer
    adapter.fallback = RuleBasedBreakExplainer()
    adapter.failed = False
    context = BreakContext("A1", "AAPL", Decimal(0), Decimal(10), Decimal(-10), ())
    assert adapter.explain(context).source == "offline"
    assert adapter.failed is True
    assert adapter.explain(context).source == 'offline'


def test_json_response_avoids_untrusted_decimal_string():
    assert dumps_exact({"text": 'a"b', "number": Decimal('2.1')}) == '{"text":"a\\"b","number":2.1}'
