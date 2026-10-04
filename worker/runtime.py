import json
import sqlite3
import threading
import uuid

from .planner import Clarification, plan
from .store import now
from .tools import APPROVAL_LIMIT, ToolError, Tools, TransientError


class Runtime:
    """Observation-driven executor. Checkpoints make every mutation safe to replay."""
    def __init__(self, store):
        self.store = store
        self.tools = Tools(store)
        self.lock = threading.RLock()

    def create(self, request, mode="offline", fault="none"):
        if not isinstance(request, str) or not request.strip() or len(request) > 2000:
            raise ValueError("Provide a task between 1 and 2,000 characters.")
        if mode not in ("offline", "llm") or fault not in ("none", "read_once", "write_once"):
            raise ValueError("Invalid planner or fault mode.")
        task_id = uuid.uuid4().hex
        state = {"mode": mode, "fault": fault, "fault_used": False, "goal": None,
                 "sources": None, "invoices": {}, "writes": {}, "evidence": {},
                 "approved_hashes": [], "attempts": {}, "summary": "Queued"}
        state["steps"] = 0
        with self.store.connect() as db:
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?)",
                       (task_id, request.strip(), "queued", json.dumps(state), now(), now()))
        return task_id

    def resume_on_startup(self):
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM tasks WHERE status IN ('queued','running')")]
        return ids

    def decide(self, task_id, approve):
        with self.lock:
            task = self.store.task(task_id)
            if task["status"] != "awaiting_approval":
                raise ValueError("This task is not waiting for approval.")
            state = task["state"]
            if not approve:
                state["summary"] = "Approval declined. No invoices were written by this task."
                self.store.save(task_id, state, "cancelled")
                self.store.event(task_id, "human", "Human declined the proposed ledger writes.")
                return False
            state["approved_hashes"] = [i["source_hash"] for i in state["invoices"].values()]
            self.store.save(task_id, state, "queued")
            self.store.event(task_id, "human", "Human approved the extracted invoice snapshot.",
                             {"approved_hashes": state["approved_hashes"]})
            return True

    def run(self, task_id):
        with self.lock:
            task = self.store.task(task_id)
            if task["status"] not in ("queued", "running"):
                return
            state = task["state"]
            self.store.save(task_id, state)
            try:
                if not state["goal"]:
                    with self.store.connect() as db:
                        vendors = [r[0] for r in db.execute("SELECT DISTINCT vendor FROM documents ORDER BY vendor")]
                    state["goal"] = plan(task["request"], vendors, state["mode"])
                    self.store.event(task_id, "plan", "Goal understood; search → read → policy check → record → verify.",
                                     {"goal": state["goal"], "planner": state["mode"]})
                    self.store.save(task_id, state)
                # Each next action depends on persisted observations, not an unconditional script.
                for _ in range(60):
                    if state.get("steps", 0) >= 60:
                        raise ToolError("Execution step budget exhausted.")
                    state["steps"] = state.get("steps", 0) + 1
                    self.store.save(task_id, state)
                    action, source_id = self.next_action(state)
                    if action == "approval":
                        state["summary"] = "Approval required: an invoice is at or above INR 100,000. Review the extracted values below."
                        self.store.save(task_id, state, "awaiting_approval")
                        self.store.event(task_id, "approval", "Paused before any writes; approval required by amount policy.",
                                         {"invoices": list(state["invoices"].values()), "threshold_minor": APPROVAL_LIMIT})
                        return
                    if action == "complete":
                        # Re-check every record, including earlier batch members, before declaring success.
                        sources = self.tools.search(state["goal"])
                        if sources != state["sources"]:
                            raise ToolError("The matching source set changed during execution. Review and submit again.")
                        for invoice in state["invoices"].values():
                            state["evidence"][invoice["source_id"]] = self.tools.verify(invoice)
                        created = sum(w["outcome"] == "created" for w in state["writes"].values())
                        existing = len(state["writes"]) - created
                        state["summary"] = f"Verified {len(state['evidence'])} invoice(s): {created} recorded, {existing} already present. Source amounts, dates and hashes match the ledger."
                        self.store.event(task_id, "complete", state["summary"], {"evidence": list(state["evidence"].values())})
                        self.store.save(task_id, state, "completed")
                        return
                    self.execute(task_id, state, action, source_id)
                raise ToolError("Execution step budget exhausted.")
            except Clarification as exc:
                state["summary"] = str(exc)
                self.store.event(task_id, "clarification", str(exc))
                self.store.save(task_id, state, "needs_input")
            except ToolError as exc:
                state["summary"] = str(exc)
                if state["writes"]:
                    state["summary"] += " Some records may already have been written; inspect the ledger and evidence before retrying."
                self.store.event(task_id, "blocked", state["summary"])
                self.store.save(task_id, state, "needs_input")
            except Exception:
                state["summary"] = "Unexpected execution failure. Inspect the server log and ledger before retrying."
                self.store.save(task_id, state, "failed")
                self.store.event(task_id, "error", state["summary"])
                import traceback
                traceback.print_exc()

    @staticmethod
    def next_action(state):
        if state["sources"] is None:
            return "search", None
        for source in state["sources"]:
            if source["id"] not in state["invoices"]:
                return "read", source["id"]
        for invoice in state["invoices"].values():
            if invoice["amount_minor"] >= APPROVAL_LIMIT and invoice["source_hash"] not in state["approved_hashes"]:
                return "approval", None
        for source in state["sources"]:
            if source["id"] not in state["writes"]:
                return "record", source["id"]
            if source["id"] not in state["evidence"]:
                return "verify", source["id"]
        return "complete", None

    def execute(self, task_id, state, action, source_id):
        key = action + ":" + (source_id or "")
        if state["attempts"].get(key, 0) >= 3:
            raise ToolError("Retry budget exhausted for " + action)
        state["attempts"][key] = state["attempts"].get(key, 0) + 1
        state["summary"] = f"Working: {action}" + (f" {source_id}" if source_id else " matching invoices") + "."
        self.store.save(task_id, state)
        self.store.event(task_id, "action", f"Using {action} tool" + (f" on {source_id}" if source_id else ""),
                         {"tool": action, "source_id": source_id, "attempt": state["attempts"][key]})
        try:
            if not state["fault_used"] and state["fault"] == {"read": "read_once", "record": "write_once"}.get(action):
                state["fault_used"] = True
                self.store.save(task_id, state)
                raise TransientError("Injected sandbox service timeout before operation committed.")
            if action == "search":
                result = self.tools.search(state["goal"])
                state["sources"] = result
            elif action == "read":
                result = self.tools.read(source_id)
                state["invoices"][source_id] = result
            elif action == "record":
                result = self.tools.record(state["invoices"][source_id], task_id, state["approved_hashes"])
                state["writes"][source_id] = result
            elif action == "verify":
                result = self.tools.verify(state["invoices"][source_id])
                state["evidence"][source_id] = result
            else:
                raise ToolError("Unknown tool requested.")
            self.store.event(task_id, "observation", f"{action.capitalize()} succeeded.", result)
            self.store.save(task_id, state)
        except (TransientError, sqlite3.OperationalError) as exc:
            if isinstance(exc, sqlite3.OperationalError) and "locked" not in str(exc).lower():
                raise
            if state["attempts"][key] >= 3:
                raise ToolError("Retry budget exhausted for " + action) from exc
            self.store.event(task_id, "retry", "Transient failure observed; retrying the same idempotent operation.",
                             {"tool": action, "attempt": state["attempts"][key], "max_attempts": 3})
            self.store.save(task_id, state)
