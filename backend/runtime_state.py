"""Small durable control plane for scheduler health and the kill switch."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from models import RuntimeState


class RuntimeStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load()

    def _load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        try:
            return RuntimeState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except Exception:
            return RuntimeState(
                last_cycle_status="state_recovered",
                last_cycle_message="The previous runtime file was unreadable; safe defaults were restored.",
            )

    def save(self) -> RuntimeState:
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(self.state.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return self.state

    def schedule(self, next_cycle_at: datetime) -> RuntimeState:
        self.state.next_cycle_at = next_cycle_at
        self.state.last_heartbeat_at = datetime.now(timezone.utc)
        return self.save()

    def record_cycle(self, status: str, message: str, *, failed: bool = False) -> RuntimeState:
        now = datetime.now(timezone.utc)
        self.state.last_heartbeat_at = now
        self.state.last_cycle_at = now
        self.state.last_cycle_status = status
        self.state.last_cycle_message = message[:500]
        self.state.consecutive_failures = self.state.consecutive_failures + 1 if failed else 0
        return self.save()

    def set_kill_switch(self, armed: bool) -> RuntimeState:
        self.state.kill_switch = armed
        self.state.last_heartbeat_at = datetime.now(timezone.utc)
        return self.save()
