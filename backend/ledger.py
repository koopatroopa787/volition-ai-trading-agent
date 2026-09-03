"""Append-only, hash-chained decision passports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from models import DecisionPassport, ExecutionEvent


class DecisionLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hash(passport: DecisionPassport) -> str:
        payload = passport.model_dump(mode="json", exclude={"record_hash"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def append(self, passport: DecisionPassport) -> DecisionPassport:
        recent = self.recent(limit=1)
        passport.previous_hash = recent[0].record_hash if recent else "GENESIS"
        passport.record_hash = self._hash(passport)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(passport.model_dump_json() + "\n")
        return passport

    def recent(self, limit: int = 20) -> list[DecisionPassport]:
        if not self.path.exists():
            return []
        rows: list[DecisionPassport] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(DecisionPassport.model_validate_json(line))
            except Exception:
                continue
        return list(reversed(rows[-limit:]))

    def verify(self) -> tuple[bool, int]:
        records = list(reversed(self.recent(limit=100_000)))
        previous = "GENESIS"
        for record in records:
            if record.previous_hash != previous or record.record_hash != self._hash(record):
                return False, len(records)
            previous = record.record_hash
        return True, len(records)


class ExecutionLedger:
    """Append-only order lifecycle evidence, separate from immutable decisions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: ExecutionEvent) -> ExecutionEvent:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
        return event

    def recent(self, limit: int = 50) -> list[ExecutionEvent]:
        if not self.path.exists():
            return []
        rows: list[ExecutionEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(ExecutionEvent.model_validate_json(line))
            except Exception:
                continue
        return list(reversed(rows[-limit:]))

    def latest_status(self, order_id: str) -> str | None:
        return next(
            (event.status for event in self.recent(limit=5_000) if event.order_id == order_id),
            None,
        )
