"""Append-only, hash-chained decision passports."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from models import DecisionPassport, ExecutionEvent


class DecisionLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hash(passport: DecisionPassport) -> str:
        payload = passport.model_dump(mode="json", exclude={"record_hash"})
        # Older passports predate per-leg quote timestamps. Keep their
        # canonical representation stable when the new optional field is absent.
        for leg in payload["plan"]["legs"]:
            if leg.get("quote_at") is None:
                leg.pop("quote_at", None)
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
        if not self.path.exists():
            return True, 0
        previous = "GENESIS"
        count = 0
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = DecisionPassport.model_validate_json(line)
                    except Exception:
                        return False, count
                    if record.previous_hash != previous or record.record_hash != self._hash(record):
                        return False, count
                    previous = record.record_hash
                    count += 1
        except (OSError, UnicodeError):
            return False, count
        return True, count


class ExecutionLedger:
    """Append-only order lifecycle evidence, separate from immutable decisions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: ExecutionEvent) -> ExecutionEvent:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
            if event.kind in {"order_intent", "exit_intent"}:
                handle.flush()
                os.fsync(handle.fileno())
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

    def active_orders(self) -> list[ExecutionEvent]:
        """Stream the whole lifecycle log so old pending orders retain their lock."""

        if not self.path.exists():
            return []
        terminal = {"filled", "canceled", "expired", "rejected", "replaced"}
        active: dict[str, ExecutionEvent] = {}
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = ExecutionEvent.model_validate_json(line)
                except Exception:
                    continue
                status = event.status.lower()
                http_code = int(status[5:]) if status.startswith("http_") and status[5:].isdigit() else None
                uncertain = status in {"submission_unknown", "transport_error"} or (
                    http_code is not None and (http_code in {408, 409, 429} or http_code >= 500)
                )
                if event.kind not in {"order_intent", "order_submitted", "order_update", "exit_intent", "exit_submitted", "exit_update"} and not uncertain:
                    continue
                key = event.order_id or event.client_order_id
                if not key:
                    continue
                if event.order_id and event.client_order_id:
                    # A submission may first be known only by client ID.
                    active.pop(event.client_order_id, None)
                definite_http_rejection = (
                    http_code is not None and 400 <= http_code < 500
                    and http_code not in {408, 409, 429}
                )
                if status in terminal or definite_http_rejection:
                    active.pop(key, None)
                elif event.order_id or event.client_order_id:
                    active[key] = event
        return list(active.values())

    def latest_status(self, order_id: str) -> str | None:
        return next(
            (event.status for event in self.recent(limit=5_000) if event.order_id == order_id),
            None,
        )
