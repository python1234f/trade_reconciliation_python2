"""In-memory event index and an atomic materialized position projection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, localcontext
from threading import RLock

from .models import EventType, TradeEvent, TradeView, VersionView

PositionKey = tuple[str, str]


@dataclass(frozen=True)
class EngineSnapshot:
    positions: dict[PositionKey, Decimal]
    uncertain: dict[PositionKey, tuple[str, ...]]
    trades: tuple[TradeView, ...]
    ingestion_errors: int


class TradeEngine:
    """All mutations and snapshots use one lock; no partially updated API views."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._events_by_id: dict[str, TradeEvent] = {}
        self._events_by_trade: dict[str, dict[str, TradeEvent]] = defaultdict(dict)
        self._corrupted_ids: set[str] = set()
        self._corrupted_keys: dict[str, set[PositionKey]] = defaultdict(set)
        self._corrupted_trades: dict[str, set[str]] = defaultdict(set)
        self._views: dict[str, TradeView] = {}
        self._positions: dict[PositionKey, Decimal] = {}
        self.errors: list[dict[str, object]] = []
        self.rejected_count = 0
        self.duplicate_count = 0
        self.id_conflict_count = 0

    def record_rejected(self, line: int, reason: str) -> None:
        with self._lock:
            self.rejected_count += 1
            
            if len(self.errors) < 100:
                self.errors.append({"line": line, "reason": reason[:300]})


    def _note_corruption(self, event: TradeEvent) -> None:
        self._corrupted_trades[event.trade_id].add(event.event_id)
        self._corrupted_keys[event.event_id].add((event.account, event.symbol))
        
        self._rebuild_trade(event.trade_id)

    def apply(self, event: TradeEvent) -> str:
        with self._lock:
            if event.event_id in self._corrupted_ids:
                self._note_corruption(event)
                return "corrupted"

            earlier = self._events_by_id.get(event.event_id)
            if earlier is not None:
                if earlier == event:
                    self.duplicate_count += 1
                    return "duplicate"
                self.id_conflict_count += 1
                del self._events_by_id[event.event_id]
                del self._events_by_trade[earlier.trade_id][event.event_id]
                self._corrupted_ids.add(event.event_id)
                self._note_corruption(earlier)
                self._note_corruption(event)
                return "event_id_conflict"

            self._events_by_id[event.event_id] = event
            self._events_by_trade[event.trade_id][event.event_id] = event
            self._rebuild_trade(event.trade_id)
            return 'accepted'

    def _rebuild_trade(self, trade_id: str) -> None:
        previous = self._views.get(trade_id)
        if previous and previous.current:
            contribution = previous.current.contribution()
            if contribution:
                self._adjust(*contribution, subtract=True)

        events = list(self._events_by_trade.get(trade_id, {}).values())
        corrupted = tuple(sorted(self._corrupted_trades.get(trade_id, set())))
        if not events and not corrupted:
            self._views.pop(trade_id, None)
            return

        grouped: dict[int, list[TradeEvent]] = defaultdict(list)
        for event in events:
            grouped[event.version].append(event)

        history: list[VersionView] = []
        conflicts: list[int] = []
        anomalies: list[str] = []
        previous_unambiguous: TradeEvent | None = None
        for version in sorted(grouped):
            candidates = tuple(sorted(grouped[version], key=lambda e: (e.event_id, e.event_time)))
            conflicted = len({e.business_signature() for e in candidates}) > 1
            history.append(VersionView(version, candidates, conflicted))
            if conflicted:
                conflicts.append(version)
                previous_unambiguous = None
            else:
                candidate = candidates[0]
                if previous_unambiguous:
                    if previous_unambiguous.type is EventType.CANCEL and candidate.type is not EventType.CANCEL:
                        anomalies.append(f"v{version}: active event after CANCEL")
                    if previous_unambiguous.type is EventType.NEW and candidate.type is EventType.NEW:
                        anomalies.append(f"v{version}: repeated NEW")
                previous_unambiguous = candidate

        highest = history[-1] if history else None
        current = highest.events[0] if highest and not highest.conflicted else None
        status = (
            'CONFLICTED' if not highest or highest.conflicted else
            "CANCELLED" if current and current.type is EventType.CANCEL else "ACTIVE"
        )
        view = TradeView(
            trade_id=trade_id,
            status=status,
            highest_version=highest.version if highest else None,
            current=current,
            history=tuple(history),
            conflicting_versions=tuple(conflicts),
            corrupted_event_ids=corrupted,
            anomalies=tuple(anomalies),
        )
        self._views[trade_id] = view
        if current:
            contribution = current.contribution()
            if contribution:
                self._adjust(*contribution)


    def _adjust(self, key: PositionKey, value: Decimal, *, subtract: bool = False) -> None:
        with localcontext() as context:
            context.prec = 96  
            result = self._positions.get(key, Decimal(0)) + (-value if subtract else value)
        if result == 0:
            self._positions.pop(key, None)
        else:
            self._positions[key] = result

    def get_trade(self, trade_id: str) -> TradeView | None:
        with self._lock:
            return self._views.get(trade_id)


    def snapshot(self) -> EngineSnapshot:
        with self._lock:
            uncertain: dict[PositionKey, set[str]] = defaultdict(set)
            for view in self._views.values():
                if view.status == "CONFLICTED":
                    for version in view.history:
                        for event in version.events:
                            uncertain[(event.account, event.symbol)].add(
                                f"trade {view.trade_id}: highest version is conflicted"
                            )
            for event_id, keys in self._corrupted_keys.items():
                for key in keys:
                    uncertain[key].add(f"event_id {event_id}: conflicting event identity")
            return EngineSnapshot(
                positions=dict(self._positions),
                uncertain={key: tuple(sorted(reasons)) for key, reasons in uncertain.items()},
                trades=tuple(self._views[key] for key in sorted(self._views)),
                ingestion_errors=self.rejected_count,
            )


    def account_positions(self, account: str) -> list[dict[str, object]]:
        snap = self.snapshot()
        keys = {key for key in snap.positions if key[0] == account}
        keys.update(key for key in snap.uncertain if key[0] == account)
        return [
            {
                'symbol': symbol,
                "quantity": snap.positions.get((account, symbol), Decimal(0)),
                "provisional": (account, symbol) in snap.uncertain,
                "warnings": list(snap.uncertain.get((account, symbol), ())),
            }
            for _, symbol in sorted(keys)
        ]

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "accepted_events": len(self._events_by_id),
                "trades": len(self._views),
                'duplicates': self.duplicate_count,
                "rejected_lines": self.rejected_count,
                'event_id_conflicts': self.id_conflict_count,
                "conflicted_trades": sum(v.status == "CONFLICTED" for v in self._views.values()),
            }
