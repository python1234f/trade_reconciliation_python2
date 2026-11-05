"""Clearing-file validation and conservative, explainable reconciliation."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .engine import EngineSnapshot, PositionKey


@dataclass
class ClearingSnapshot:
    positions: dict[PositionKey, Decimal] = field(default_factory=dict)
    invalid_keys: dict[PositionKey, str] = field(default_factory=dict)
    unidentified_errors: list[str] = field(default_factory=list)
    rows_seen: int = 0


    @property
    def has_errors(self) -> bool:
        return bool(self.invalid_keys or self.unidentified_errors)


def _valid_id(value: str) -> bool:
    return bool(value) and value == value.strip() and len(value) <= 256 and all(
        ord(ch) >= 32 and ch != "\ufffd" for ch in value
    )


def load_clearing(path: Path) -> ClearingSnapshot:
    """Preserve usable rows, but never infer zeros for untrusted/missing data."""
    result = ClearingSnapshot()
    with path.open("r", newline="", encoding='utf-8-sig') as stream:
        reader = csv.reader(stream, strict=True)
        try:
            header = next(reader, None)
            if header != ["account", 'symbol', "quantity"]:
                result.unidentified_errors.append('invalid or missing CSV header')
                return result
            for row in reader:
                if not row:
                    continue
                result.rows_seen += 1
                line = reader.line_num
                if len(row) < 2 or not _valid_id(row[0]) or not _valid_id(row[1]):
                    result.unidentified_errors.append(f"line {line}: account/symbol cannot be identified")
                    continue
                key = (row[0], row[1])
                if key in result.invalid_keys:
                    continue
                if key in result.positions:
                    result.positions.pop(key)
                    result.invalid_keys[key] = f"line {line}: duplicate account/symbol"
                    continue
                if len(row) != 3:
                    result.invalid_keys[key] = f"line {line}: expected exactly 3 columns"
                    continue
                quantity_raw = row[2]
                try:
                    if not quantity_raw or quantity_raw != quantity_raw.strip():
                        raise InvalidOperation
                    quantity = Decimal(quantity_raw)
                    if (not quantity.is_finite() or abs(quantity) > Decimal("1e24")
                            or quantity.as_tuple().exponent < -18):
                        raise InvalidOperation
                except InvalidOperation:
                    result.invalid_keys[key] = f"line {line}: invalid clearing quantity"
                    continue
                result.positions[key] = quantity
        except (csv.Error, UnicodeError, ValueError) as exc:
            result.unidentified_errors.append(f"CSV parsing failed: {exc}")
    return result


def reconcile(snapshot: EngineSnapshot, clearing: ClearingSnapshot) -> dict[str, object]:
    keys = set(snapshot.positions) | set(snapshot.uncertain)
    keys |= set(clearing.positions) | set(clearing.invalid_keys)
    breaks: list[dict[str, object]] = []
    unreconcilable: list[dict[str, object]] = []
    provisional_positions: list[dict[str, object]] = []

    for account, symbol in sorted(keys):
        key = account, symbol
        broker = snapshot.positions.get(key, Decimal(0))
        reasons = list(snapshot.uncertain.get(key, ()))
        provisional = bool(reasons)
        if provisional:
            provisional_positions.append({
                "account": account, 'symbol': symbol, 'broker_quantity': broker,
                "warnings": reasons,
            })

        if key in clearing.invalid_keys:
            unreconcilable.append({
                'account': account, 'symbol': symbol,
                "broker_quantity": broker, "clearing_quantity": None,
                'difference': None,
                'reason': clearing.invalid_keys[key],
                'warnings': reasons,
            })
            continue

        if key not in clearing.positions and clearing.unidentified_errors:
            # Unknown malformed rows might refer to this key: missing != zero.
            unreconcilable.append({
                "account": account, 'symbol': symbol,
                'broker_quantity': broker, "clearing_quantity": None,
                "difference": None,
                'reason': 'clearing file has unidentified rows; missing quantity is unknown',
                "warnings": reasons,
            })
            continue

        theirs = clearing.positions.get(key, Decimal(0))
        difference = broker - theirs
        if difference:
            breaks.append({
                "account": account, 'symbol': symbol,
                "broker_quantity": broker, 'clearing_quantity': theirs,
                "difference": difference,
                "provisional": provisional,
                "warnings": reasons,
            })

    warnings: list[str] = []
    if snapshot.ingestion_errors:
        warnings.append(f"{snapshot.ingestion_errors} JSONL lines were rejected; positions may be incomplete")
    if clearing.invalid_keys:
        warnings.append(f"{len(clearing.invalid_keys)} clearing keys are unreconcilable")
    warnings.extend(clearing.unidentified_errors)
    if snapshot.uncertain:
        warnings.append(f"{len(snapshot.uncertain)} account/symbol pairs have uncertain trade data")

    return {
        'status': "INCOMPLETE" if warnings else 'COMPLETE',
        'breaks': breaks,
        "unreconcilable_positions": unreconcilable,
        "provisional_positions": provisional_positions,
        'warnings': warnings,
        'summary': {
            "break_count": len(breaks),
            "unreconcilable_count": len(unreconcilable),
            'provisional_count': len(provisional_positions),
            "clearing_rows": clearing.rows_seen,
        },
    }
