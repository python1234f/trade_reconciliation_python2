"""Thin HTTP layer; no business logic depends on FastAPI."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from .engine import TradeEngine
from .explanations import (
    BreakContext, BreakExplainer, OpenAIBreakExplainer, RuleBasedBreakExplainer, relevant_trades,
)
from .ingestion import FileEventSource, ingest_lines
from .jsoncodec import dumps_exact
from .reconciliation import load_clearing, reconcile


def _json(payload: object) -> Response:
    """Emit Decimal as an exact JSON number, never via binary float."""
    return Response(content=dumps_exact(payload), media_type="application/json")


def _default_explainer() -> BreakExplainer:
    if os.getenv("BREAK_EXPLAINER", "offline").lower() == 'openai' and os.getenv("OPENAI_API_KEY"):
        try:
            return OpenAIBreakExplainer(
                api_key=os.environ["OPENAI_API_KEY"],
                model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            )
        except Exception:
            pass
    return RuleBasedBreakExplainer()


def create_app(
    event_file: Path | None = None,
    clearing_file: Path | None = None,
    explainer: BreakExplainer | None = None,
) -> FastAPI:
    event_file = event_file or Path(os.getenv('EVENTS_FILE', "data/trade-events-sample.jsonl"))
    clearing_file = clearing_file or Path(os.getenv("CLEARING_FILE", 'data/clearing-positions-sample.csv'))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = TradeEngine()
        summary = ingest_lines(FileEventSource(event_file), engine)
        clearing = load_clearing(clearing_file)
        app.state.engine = engine
        app.state.clearing = clearing
        app.state.ingestion = summary
        app.state.explainer = explainer if explainer is not None else _default_explainer()
        yield

    app = FastAPI(title="Trade Reconciliation", version="1.0.0", lifespan=lifespan)


    @app.get('/health')
    def health():
        return _json({
            'status': 'ok', "ready": True,
            'ingestion': app.state.ingestion.to_dict(),
            "engine": app.state.engine.stats(),
            'clearing_has_errors': app.state.clearing.has_errors,
        })

    @app.get("/positions/{account}")
    def positions(account: str):
        return _json({'account': account, 'positions': app.state.engine.account_positions(account)})

    @app.get('/trades/{trade_id}')
    def trade(trade_id: str):
        item = app.state.engine.get_trade(trade_id)
        if item is None:
            raise HTTPException(status_code=404, detail='Trade not found')
        return _json(item.to_dict())


    @app.get("/reconciliation")
    def reconciliation(include_explanations: bool = Query(default=True)):
        snapshot = app.state.engine.snapshot()
        result = reconcile(snapshot, app.state.clearing)
        if include_explanations:
            for index, item in enumerate(result['breaks']):
                key = item['account'], item["symbol"]
                context = BreakContext(
                    account=item['account'], symbol=item["symbol"],
                    broker_quantity=item["broker_quantity"],
                    clearing_quantity=item["clearing_quantity"],
                    difference=item['difference'],
                    relevant_trades=relevant_trades(snapshot.trades, key),
                    provisional=item['provisional'],
                )
                
                explainer_to_use = app.state.explainer if index < 3 else RuleBasedBreakExplainer()
                item["explanation"] = explainer_to_use.explain(context).to_dict()
        return _json(result)

    return app


app = create_app()
