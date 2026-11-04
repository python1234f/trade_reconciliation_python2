"""Source-agnostic ingestion; file is one implementation of an iterable source."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator

from pydantic import ValidationError

from .engine import TradeEngine
from .models import TradeEvent


@dataclass(frozen=True)
class FileEventSource:
    path: Path


    def __iter__(self) -> Iterator[bytes]:
        with self.path.open("rb") as file:
            yield from file


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def parse_event(line: str | bytes) -> TradeEvent:
    if isinstance(line, bytes):
        line = line.decode("utf-8")  
    data = json.loads(
        line,
        parse_float=Decimal,
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_keys,
    )
    if not isinstance(data, dict):
        raise ValueError('top-level JSON value must be an object')
    return TradeEvent.model_validate(data)


@dataclass(frozen=True)
class IngestionSummary:
    lines: int
    accepted: int
    duplicates: int
    rejected: int
    id_conflicts: int
    previously_corrupted: int

    def to_dict(self) -> dict[str, int]:
        return vars(self)


def ingest_lines(source: Iterable[bytes | str], engine: TradeEngine) -> IngestionSummary:
    counts = {
        'lines': 0, "accepted": 0, "duplicates": 0,
        "rejected": 0, 'id_conflicts': 0, "previously_corrupted": 0,
    }
    for line_no, line in enumerate(source, start=1):
        counts["lines"] += 1
        if len(line) > 1_000_000:
            counts["rejected"] += 1
            engine.record_rejected(line_no, "JSONL line exceeds 1 MB")
            continue
        try:
            event = parse_event(line)
        except (ValueError, UnicodeDecodeError, ValidationError, InvalidOperation) as exc:
            counts['rejected'] += 1
            engine.record_rejected(line_no, str(exc))
            continue
        outcome = engine.apply(event)
        counts[{
            "accepted": "accepted",
            'duplicate': "duplicates",
            "event_id_conflict": 'id_conflicts',
            "corrupted": "previously_corrupted",
        }[outcome]] += 1
    return IngestionSummary(**counts)
