"""Synthetic company mailbox. These are real files read by the tools."""
from pathlib import Path

FIXTURES = [
    ("acme-sep", "Acme", "2026-09-01", "AC-009", "9,800.00", "2026-09-20", 1),
    ("acme-oct", "Acme", "2026-10-01", "AC-010", "12,500.00", "2026-10-15", 1),
    ("globex-oct", "Globex", "2026-10-02", "GL-101", "175,000.00", "2026-10-25", 1),
    ("initech-oct", "Initech", "2026-10-01", "IN-203", "4,200.50", "2026-10-18", 1),
    ("vertex-oct", "Vertex", "2026-10-02", "VX-018", "8,700.00", "NOT PROVIDED", 1),
    ("acme-paid", "Acme", "2026-08-01", "AC-008", "5,000.00", "2026-08-20", 0),
]


def seed(store):
    folder = store.root / "mailbox"
    folder.mkdir(exist_ok=True)
    with store.connect() as db:
        for doc_id, vendor, issued, number, amount, due, unpaid in FIXTURES:
            filename = doc_id + ".txt"
            path = folder / filename
            # Never overwrite user edits or an intentionally damaged source on restart.
            if not path.exists():
                path.write_text(
                    f"Vendor: {vendor}\nInvoice: {number}\nIssued: {issued}\n"
                    f"Amount: INR {amount}\nDue: {due}\n"
                    f"Status: {'unpaid' if unpaid else 'paid'}\n", encoding="utf-8")
            db.execute("INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?)",
                       (doc_id, vendor, issued, filename, unpaid))

