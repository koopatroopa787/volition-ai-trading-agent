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


if __name__ == "__main__":
    unittest.main()
