import hashlib
import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation


class ToolError(Exception):
    pass


class TransientError(ToolError):
    pass


FIELDS = ("vendor", "invoice_number", "amount_minor", "currency", "issued", "due", "source_hash")
APPROVAL_LIMIT = 10_000_000  # INR 100,000, represented in paise.


class Tools:
    def __init__(self, store):
        self.store = store

    def search(self, goal):
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT id,vendor,issued,unpaid FROM documents WHERE vendor=? ORDER BY issued DESC,id",
                (goal["vendor"],))]
        if goal["selection"] == "all_unpaid":
            rows = [r for r in rows if r["unpaid"]]
        elif rows:
            if len(rows) > 1 and rows[0]["issued"] == rows[1]["issued"]:
                raise ToolError("Two invoices share the latest issue date. A human must choose the source.")
            rows = rows[:1]
        if not rows:
            raise ToolError("No matching invoice documents found.")
        return rows

    def read(self, source_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise ToolError("Source document no longer exists.")
        folder = (self.store.root / "mailbox").resolve()
        path = (folder / row["filename"]).resolve()
        if path.parent != folder:
            raise ToolError("Document path is outside the sandbox mailbox.")
        try:
            content = path.read_bytes()
            text = content.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ToolError("Document could not be read as UTF-8 text.") from exc
        pairs = {}
        for line in text.splitlines():
            if ":" not in line:
                continue  # Untrusted document prose is never executed as instructions.
            key, val = line.split(":", 1)
            if key in pairs:
                raise ToolError("Duplicate invoice field: " + key)
            pairs[key] = val.strip()
        try:
            amount = re.fullmatch(r"INR ([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)", pairs["Amount"])
            if not amount:
                raise ValueError("Invalid amount")
            minor = int(Decimal(amount[1].replace(",", "")) * 100)
            issued = date.fromisoformat(pairs["Issued"])
            due = date.fromisoformat(pairs["Due"])
            if minor <= 0 or due < issued or pairs["Vendor"] != row["vendor"] or str(issued) != row["issued"]:
                raise ValueError("Invalid invoice fields")
            if pairs["Status"] not in ("paid", "unpaid") or bool(row["unpaid"]) != (pairs["Status"] == "unpaid"):
                raise ValueError("Status mismatch")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pairs["Invoice"]):
                raise ValueError("Invalid invoice number")
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise ToolError("Missing or invalid amount, due date, issue date, vendor, invoice number, or status. Correct the source document and submit again.") from exc
        return {"source_id": source_id, "vendor": pairs["Vendor"], "invoice_number": pairs["Invoice"],
                "amount_minor": minor, "currency": "INR", "issued": str(issued), "due": str(due),
                "source_hash": hashlib.sha256(content).hexdigest()}

    def record(self, invoice, task_id, approved_hashes):
        from .store import now
        # This policy is enforced at the mutation boundary, independently of the planner.
        fresh = self.read(invoice["source_id"])
        if fresh != invoice:
            raise ToolError("The source changed after extraction. No write was performed.")
        if invoice["amount_minor"] >= APPROVAL_LIMIT and invoice["source_hash"] not in approved_hashes:
            raise ToolError("Human approval is required for this exact invoice before writing.")
        with self.store.connect() as db:
            existing = db.execute("SELECT * FROM ledger WHERE source_id=?", (invoice["source_id"],)).fetchone()
            if existing:
                if any(existing[k] != invoice[k] for k in FIELDS):
                    raise ToolError("An existing ledger entry conflicts with the source. Human reconciliation required.")
                return {"outcome": "already_present", "source_id": invoice["source_id"]}
            try:
                db.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    invoice["source_id"], invoice["vendor"], invoice["invoice_number"],
                    invoice["amount_minor"], invoice["currency"], invoice["issued"], invoice["due"],
                    invoice["source_hash"], task_id, now()))
            except sqlite3.IntegrityError as exc:
                raise ToolError("A ledger entry already uses this vendor and invoice number. Human reconciliation required.") from exc
        return {"outcome": "created", "source_id": invoice["source_id"]}

    def verify(self, invoice):
        fresh = self.read(invoice["source_id"])
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM ledger WHERE source_id=?", (invoice["source_id"],)).fetchone()
        if fresh != invoice or not row or any(row[k] != fresh[k] for k in FIELDS):
            raise ToolError("Verification failed: the current source and saved ledger entry do not match.")
        return {"source_id": invoice["source_id"], "invoice_number": invoice["invoice_number"],
                "source_hash": invoice["source_hash"], "checked_fields": list(FIELDS),
                "amount_minor": invoice["amount_minor"], "currency": invoice["currency"],
                "due": invoice["due"], "matched": True}

