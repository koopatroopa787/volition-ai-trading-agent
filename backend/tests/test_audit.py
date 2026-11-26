from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from audit import AuditTrail


class AuditTrailTests(unittest.TestCase):
    def test_events_are_searchable_chained_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="volition-audit-") as directory:
            audit = AuditTrail(Path(directory) / "audit.sqlite3", sensitive_values=["a-secret-value"])
            audit.append("model_request", {
                "messages": [{"role": "user", "content": "market evidence"}],
                "headers": {"Authorization": "Bearer private", "APCA-API-SECRET-KEY": "private"},
                "note": "received a-secret-value in free text",
            }, cycle_id="cycle-1", symbol="SPY")
            audit.append("model_response", {"content": "visible rationale"}, cycle_id="cycle-1", symbol="SPY")
            self.assertEqual(audit.verify(), (True, 2))
            rows = audit.events(cycle_id="cycle-1")
            self.assertEqual([row["kind"] for row in rows], ["model_request", "model_response"])
            self.assertEqual(rows[0]["payload"]["headers"]["Authorization"], "[REDACTED]")
            self.assertEqual(rows[0]["payload"]["note"], "received [REDACTED] in free text")
            self.assertEqual(rows[1]["previous_hash"], rows[0]["record_hash"])
            self.assertNotIn(b"private", audit.path.read_bytes())

    def test_verification_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory(prefix="volition-audit-") as directory:
            audit = AuditTrail(Path(directory) / "audit.sqlite3")
            audit.append("candidate_screen", {"selected": "SPY"})
            with sqlite3.connect(audit.path) as db:
                db.execute("UPDATE events SET kind = 'changed' WHERE id = 1")
            self.assertEqual(audit.verify(), (False, 0))


class AuditArchiveTests(unittest.TestCase):
    @staticmethod
    def _backdate(audit: AuditTrail, day: str) -> None:
        # Rehash the whole chain with the earliest rows moved to ``day``.
        with sqlite3.connect(audit.path) as db:
            rows = db.execute("SELECT id, recorded_at, cycle_id, symbol, kind, payload_zlib FROM events ORDER BY id").fetchall()
            previous = "GENESIS"
            for index, (row_id, recorded_at, cycle_id, symbol, kind, packed) in enumerate(rows):
                stamp = f"{day}T12:00:0{index}+00:00" if index < 2 else recorded_at
                digest = AuditTrail._hash(previous, stamp, cycle_id, symbol, kind, packed)
                db.execute("UPDATE events SET recorded_at=?, previous_hash=?, record_hash=? WHERE id=?",
                           (stamp, previous, digest, row_id))
                previous = digest

    def test_closed_days_move_to_verified_archive_and_chain_continues(self) -> None:
        with tempfile.TemporaryDirectory(prefix="volition-audit-") as directory:
            audit = AuditTrail(Path(directory) / "audit.sqlite3")
            for index in range(3):
                audit.append("market_snapshots", {"index": index, "quotes": [1.5] * 50}, cycle_id=f"c{index}")
            self._backdate(audit, "2026-11-20")
            self.assertEqual(audit.archive_closed_days(), ["2026-11-20"])
            self.assertEqual(audit.count(), 1)
            audit.append("scheduler_tick", {"market_open": False})
            self.assertEqual(audit.verify(), (True, 4))
            export = Path(directory) / "export.jsonl"
            self.assertEqual(audit.export_jsonl(export), 4)
            lines = export.read_text("utf-8").splitlines()
            self.assertIn('"index":0', lines[0])
            self.assertEqual(audit.archive_closed_days(), [])
            self.assertGreater(audit.storage()["archive_bytes"], 0)

    def test_archive_tampering_and_crash_recovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="volition-audit-") as directory:
            audit = AuditTrail(Path(directory) / "audit.sqlite3")
            for index in range(3):
                audit.append("model_request", {"index": index})
            self._backdate(audit, "2026-11-20")
            folder = audit.archive_dir / "2026-11-20"
            audit._write_day(folder, 1, 2)  # simulate a crash before the rows were deleted
            self.assertEqual(audit.archive_closed_days(), ["2026-11-20"])
            self.assertEqual(audit.verify(), (True, 3))
            (folder / "audit.jsonl.xz").write_bytes(b"tampered")
            self.assertEqual(audit.verify(), (False, 0))

    def test_broken_chain_is_not_archived(self) -> None:
        with tempfile.TemporaryDirectory(prefix="volition-audit-") as directory:
            audit = AuditTrail(Path(directory) / "audit.sqlite3")
            for index in range(3):
                audit.append("model_request", {"index": index})
            self._backdate(audit, "2026-11-20")
            with sqlite3.connect(audit.path) as db:
                db.execute("UPDATE events SET kind='edited' WHERE id=1")
            with self.assertRaises(RuntimeError):
                audit.archive_closed_days()
            self.assertEqual(audit.count(), 3)


if __name__ == "__main__":
    unittest.main()
