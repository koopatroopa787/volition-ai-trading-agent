"""Private operator export of the complete audit trail as JSONL.

Run inside the backend container or with access to its persistent data volume.
Never serve the exported file from the public web directory.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from audit import AuditTrail


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and export Volition's private audit trail")
    parser.add_argument("--database", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "audit.sqlite3")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = AuditTrail(args.database)
    valid, count = audit.verify()
    if not valid:
        raise SystemExit(f"Audit chain failed verification after {count} events; export stopped")
    written = audit.export_jsonl(args.output)
    print(f"Exported {written} verified events to {args.output}")


if __name__ == "__main__":
    main()
