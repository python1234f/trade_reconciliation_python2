# Trade reconciliation

Small, offline-first trading-execution state engine with FastAPI. Python 3.11+.

## Run

```bash
docker compose up --build
```

Open <http://localhost:8000/docs> (API), <http://localhost:8000/reconciliation> (report),
<http://localhost:8000/health> (health). The bundled sample yields exactly **one break**:
`ACC-03 / TSLA`, broker `0`, clearing `10`, difference `-10`.

Run tests in Docker (no API key or external service):

```bash
docker compose run --build --rm reconciliation pytest -q
```

Alternatively, with [`uv`](https://docs.astral.sh/uv/): `make run`, `make test`, `make lint`.
Place larger files in `data/` and override `EVENTS_FILE` / `CLEARING_FILE` (container paths,
e.g. `/app/data/my-trades.jsonl`). The container mounts `./data` read-only.

Endpoints: `GET /positions/{account}`, `GET /trades/{trade_id}`,
`GET /reconciliation?include_explanations=true`, `GET /health`.
Quantities are exact decimal JSON **numbers**, prices are decimal **strings**.

## Assumptions and behavior

- Each event is a **complete snapshot** of a trade version, not a patch; `trade_id` and
  `event_id` are globally unique identifiers (case-sensitive). Identifiers with surrounding
  whitespace are rejected rather than silently normalized. Extra JSON fields are ignored.
- Higher `version` always supersedes lower, regardless of arrival or `event_time`;
  gaps and initial AMEND/CANCEL are accepted. CANCEL is not necessarily terminal.
  NEW/AMEND require positive quantity/price; CANCEL accepts zeros. Fractional shares
  are supported with up to 18 fractional digits and magnitude 1e24, internally
  with `Decimal`. UTC timestamps are mandatory. JSONL lines over 1 MB are rejected.
- Identical redeliveries are ignored. Two different payloads for the same `event_id`
  **corrupt** that ID: neither event is trusted, and the original is revoked.
  For a `(trade_id, version)`, matching business states with different event IDs share
  one logical version; different states mark that version as conflicted.
- Only the **highest observed version** can determine current state. If it is
  conflicted, state is `CONFLICTED` with **unknown actual contribution**; its numeric
  contribution is provisionally treated as zero, and potentially affected account/symbol
  pairs are flagged. Older version conflicts remain visible without overriding a
  later unambiguous version. Corrupted event IDs conservatively flag observed pairs.
- Position = sum of latest active trade contributions. BUY positive, SELL negative,
  CANCEL zero. Reported positions describe the **supplied execution set**, not
  holdings including opening balances, transfers, settlement or corporate actions.
- Clearing is treated as a complete snapshot *if valid*: missing positions mean zero;
  difference = **broker - clearing**. Invalid/duplicate keyed rows are
  `unreconcilable_positions` (not zero). Unidentifiable damaged CSV rows prevent
  missing-as-zero inference, but valid known-key rows can still be reconciled.
- Ingestion continues past invalid JSONL lines; diagnostics are counted, the first
  100 examples retained. Reconciliation status becomes `INCOMPLETE` for invalid input,
  unresolved uncertainty, or clearing errors. Numeric breaks are still returned for
  safely comparable pairs, with `provisional` warnings when needed.
- Data is loaded once on service startup. End-of-day is interpreted as one provided
  execution batch and one clearing snapshot; no trading-date or as-of semantics are inferred.

## Explanations and availability

Default explanations are deterministic and offline: **fact → hypothesis → suggested action**.
Historical account/symbol relationships are included. Optional OpenAI adapter:
`BREAK_EXPLAINER=openai OPENAI_API_KEY=... docker compose up --build`.
Without a key, on timeout, invalid output or provider failure, the offline generator
is used. The API limits external calls to three per reconciliation request; the
adapter has a 2-second timeout, zero retries and a simple circuit breaker.
Tests never use the network or require a key.

## Trade-offs, production and alerts

- In-memory state, single worker, full per-trade re-evaluation after changes:
  prioritizes auditability and correctness over peak throughput. Recovery requires
  replaying a complete event file. For production, use durable event storage with
  unique event IDs, transactional position projections, snapshots and replay support.
- Source-agnostic `ingest_lines` accepts any iterable of event bytes/strings; swap
  the file source for a stream consumer without changing trade processing.
- Next: trading-day boundaries, checkpointed ingestion, full reconciliation run
  history, a retention policy, explicit operations workflow, and bounded LLM budgets.
- Monitor/alert on dropped/invalid event rate, duplicate-ID conflicts, highest-version
  conflicts, missing or late input, reconciliation failures/aging breaks, memory growth,
  API latency/error rate, and mismatch between materialized and fully recomputed positions.
