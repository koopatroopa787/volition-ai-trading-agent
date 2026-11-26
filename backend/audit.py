"""Durable, private, append-only evidence for Volition's decision pipeline.

The public decision passport remains compact. This store keeps the exact
normalized inputs and visible model exchanges needed to replay an investigation.
It never records request headers or configured API credentials.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import os
import shutil
import sqlite3
import zlib
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


_PRIVATE_KEYS = (
    "api_key", "apikey", "secret", "password", "authorization", "credential",
    "access_token", "refresh_token", "private_key", "cookie",
)


def _safe(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, BaseModel):
        return _safe(value.model_dump(mode="json"), secrets)
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if any(part in str(key).lower() for part in _PRIVATE_KEYS) else _safe(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item, secrets) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    raise TypeError(f"Unsupported audit value: {type(value).__name__}")


class AuditTrail:
    """SQLite event chain; each insert is committed before returning to trading code."""

    def __init__(self, path: Path, *, sensitive_values: list[str] | None = None) -> None:
        self.path = path
        self._secrets = tuple(value for value in (sensitive_values or []) if len(value) >= 8)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    cycle_id TEXT,
                    symbol TEXT,
                    kind TEXT NOT NULL,
                    payload_zlib BLOB NOT NULL,
                    previous_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS events_cycle ON events(cycle_id, id)")
            db.execute("CREATE INDEX IF NOT EXISTS events_kind ON events(kind, id)")
        if os.name == "posix":
            self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _hash(previous: str, recorded_at: str, cycle_id: str | None,
              symbol: str | None, kind: str, payload: bytes) -> str:
        metadata = json.dumps(
            [recorded_at, cycle_id, symbol, kind], separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(previous.encode("ascii") + b"\n" + metadata + b"\n" + payload).hexdigest()

    def append(self, kind: str, payload: Any, *, cycle_id: str | None = None,
               symbol: str | None = None) -> int:
        if not kind:
            raise ValueError("Audit event kind is required")
        recorded_at = datetime.now(timezone.utc).isoformat()
        canonical = json.dumps(_safe(payload, self._secrets), sort_keys=True, separators=(",", ":"), allow_nan=False)
        packed = zlib.compress(canonical.encode("utf-8"), level=6)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
            previous = row[0] if row else self._archived_tip()
            digest = self._hash(previous, recorded_at, cycle_id, symbol, kind, packed)
            cursor = db.execute(
                """INSERT INTO events
                   (recorded_at, cycle_id, symbol, kind, payload_zlib, previous_hash, record_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (recorded_at, cycle_id, symbol, kind, packed, previous, digest),
            )
            return int(cursor.lastrowid)

    @property
    def archive_dir(self) -> Path:
        return self.path.parent / "archive"

    def _manifests(self) -> list[Path]:
        return sorted(self.archive_dir.glob("*/manifest.json"))

    def _archived_tip(self) -> str:
        manifests = self._manifests()
        return json.loads(manifests[-1].read_text("utf-8"))["last_record_hash"] if manifests else "GENESIS"

    def archive_closed_days(self, today: date | None = None) -> list[str]:
        """Move each finished UTC day to ``archive/DAY/audit.jsonl.xz``.

        Compressing a whole day together is about 4x smaller than per-row zlib,
        so the live database only holds today. Each row's chain link is checked
        before it leaves SQLite; afterwards the day is covered by its manifest
        (archive sha256, first/last record hash, previous manifest sha256).
        """

        today_text = (today or datetime.now(timezone.utc).date()).isoformat()
        archived: list[str] = []
        while True:
            with self._connect() as db:
                first = db.execute("SELECT id, substr(recorded_at, 1, 10) FROM events ORDER BY id LIMIT 1").fetchone()
                if not first or first[1] >= today_text:
                    break
                day = first[1]
                # Rows written a moment out of order around midnight ride along with this day.
                cut = db.execute("SELECT MAX(id) FROM events WHERE substr(recorded_at, 1, 10) <= ?",
                                 (day,)).fetchone()[0]
            folder = self.archive_dir / day
            manifest_path = folder / "manifest.json"
            done = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else None
            if done and done["last_id"] >= first[0]:
                cut = done["last_id"]  # crashed between writing the day and deleting its rows
            else:
                self._write_day(folder, first[0], cut)
            with self._connect() as db:
                db.execute("DELETE FROM events WHERE id <= ?", (cut,))
            archived.append(day)
        if archived:
            with self._connect() as db:
                db.execute("VACUUM")
        return archived

    def _write_day(self, folder: Path, first_id: int, last_id: int) -> None:
        manifests = self._manifests()
        previous_manifest = hashlib.sha256(manifests[-1].read_bytes()).hexdigest() if manifests else None
        previous = first_previous = self._archived_tip()
        folder.mkdir(parents=True, exist_ok=True)
        archive = folder / "audit.jsonl.xz"
        partial = folder / "audit.jsonl.xz.partial"
        rows = 0
        with self._connect() as db, lzma.open(partial, "wt", encoding="utf-8", preset=6) as output:
            for row in db.execute(
                "SELECT id, recorded_at, cycle_id, symbol, kind, payload_zlib, previous_hash, record_hash "
                "FROM events WHERE id BETWEEN ? AND ? ORDER BY id", (first_id, last_id)
            ):
                if row[6] != previous or row[7] != self._hash(row[6], row[1], row[2], row[3], row[4], row[5]):
                    raise RuntimeError(f"Audit chain broken at event {row[0]}; refusing to archive")
                output.write(json.dumps(self._row_dict(row), separators=(",", ":"), ensure_ascii=False) + "\n")
                previous = row[7]
                rows += 1
        with partial.open("r+b") as handle:
            os.fsync(handle.fileno())
        partial.replace(archive)
        manifest = {
            "day": folder.name, "rows": rows, "first_id": first_id, "last_id": last_id,
            "first_previous_hash": first_previous, "last_record_hash": previous,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "previous_manifest_sha256": previous_manifest,
        }
        temporary = folder / "manifest.json.partial"
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True), "utf-8")
        temporary.replace(folder / "manifest.json")

    def _archived_lines(self):
        for manifest in self._manifests():
            with lzma.open(manifest.parent / "audit.jsonl.xz", "rt", encoding="utf-8") as lines:
                yield from lines

    def verify(self) -> tuple[bool, int]:
        previous = "GENESIS"
        prior_manifest = None
        count = 0
        for manifest_path in self._manifests():
            raw = manifest_path.read_bytes()
            manifest = json.loads(raw)
            archive = manifest_path.parent / "audit.jsonl.xz"
            if (manifest["previous_manifest_sha256"] != prior_manifest
                    or manifest["first_previous_hash"] != previous
                    or not archive.exists()
                    or hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["archive_sha256"]):
                return False, count
            previous = manifest["last_record_hash"]
            prior_manifest = hashlib.sha256(raw).hexdigest()
            count += manifest["rows"]
        with self._connect() as db:
            for recorded_at, cycle_id, symbol, kind, packed, prior, digest in db.execute(
                "SELECT recorded_at, cycle_id, symbol, kind, payload_zlib, previous_hash, record_hash "
                "FROM events ORDER BY id"
            ):
                if prior != previous or digest != self._hash(prior, recorded_at, cycle_id, symbol, kind, packed):
                    return False, count
                try:
                    json.loads(zlib.decompress(packed))
                except (ValueError, zlib.error):
                    return False, count
                previous = digest
                count += 1
        return True, count

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def storage(self) -> dict[str, int]:
        return {
            "audit_bytes": self.path.stat().st_size,
            "archive_bytes": sum(item.stat().st_size for item in self.archive_dir.glob("*/*") if item.is_file()),
            "disk_free_bytes": shutil.disk_usage(self.path.parent).free,
        }

    def export_jsonl(self, destination: Path) -> int:
        """Write a complete chronological export without truncating older events."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with destination.open("w", encoding="utf-8") as output:
            for line in self._archived_lines():
                output.write(line)
                count += 1
            with self._connect() as db:
                for row in db.execute(
                    "SELECT id, recorded_at, cycle_id, symbol, kind, payload_zlib, previous_hash, record_hash "
                    "FROM events ORDER BY id"
                ):
                    output.write(json.dumps(self._row_dict(row), separators=(",", ":"), ensure_ascii=False) + "\n")
                    count += 1
        return count

    @staticmethod
    def _row_dict(row: tuple) -> dict[str, Any]:
        return {
            "id": row[0], "recorded_at": row[1], "cycle_id": row[2], "symbol": row[3],
            "kind": row[4], "payload": json.loads(zlib.decompress(row[5])),
            "previous_hash": row[6], "record_hash": row[7],
        }

    def events(self, *, cycle_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10_000:
            raise ValueError("Audit event limit must be between 1 and 10,000")
        query = (
            "SELECT id, recorded_at, cycle_id, symbol, kind, payload_zlib, previous_hash, record_hash "
            "FROM events"
        )
        params: tuple[Any, ...] = (limit,)
        if cycle_id is not None:
            query += " WHERE cycle_id = ?"
            params = (cycle_id, limit)
        query += " ORDER BY id DESC LIMIT ?"
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._row_dict(row) for row in reversed(rows)]
