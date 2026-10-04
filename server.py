"""Local-only web UI and simulated company application's API."""
import argparse
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from worker.fixtures import seed
from worker.runtime import Runtime
from worker.store import Store

ROOT = Path(__file__).resolve().parent


def make_server(data_dir, port=8765):
    store = Store(data_dir)
    seed(store)
    runtime = Runtime(store)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="worker")
    csrf = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, payload, status=200, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path in ("/", "/app.js", "/style.css"):
                    filename, mime = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "application/javascript"),
                                      "/style.css": ("style.css", "text/css")}[path]
                    self.send((ROOT / "web" / filename).read_bytes(), content_type=mime + "; charset=utf-8")
                elif path == "/api/session":
                    configured = bool(os.environ.get("WORKER_API_KEY") and os.environ.get("WORKER_MODEL"))
                    self.send({"csrf": csrf, "sandbox": True, "llm_configured": configured,
                               "model": os.environ.get("WORKER_MODEL", "") if configured else ""})
                elif path == "/api/ledger":
                    self.send(store.ledger())
                elif path == "/api/documents":
                    with store.connect() as db:
                        self.send([dict(r) for r in db.execute("SELECT id,vendor,issued,unpaid FROM documents ORDER BY vendor,issued DESC")])
                elif path == "/api/tasks":
                    with store.connect() as db:
                        self.send([dict(r) for r in db.execute("SELECT id,request,status,updated_at FROM tasks ORDER BY created_at DESC LIMIT 50")])
                elif path.startswith("/api/tasks/"):
                    parts = path.split("/")
                    task = store.task(parts[3])
                    if len(parts) == 5 and parts[4] == "evidence":
                        self.send({"task_id": task["id"], "request": task["request"], "status": task["status"],
                                   "goal": task["state"]["goal"], "summary": task["state"]["summary"],
                                   "verification": list(task["state"]["evidence"].values()), "events": task["events"]})
                    elif len(parts) == 4:
                        self.send(task)
                    else:
                        self.send({"error": "Not found"}, 404)
                else:
                    self.send({"error": "Not found"}, 404)
            except KeyError:
                self.send({"error": "Task not found"}, 404)

        def do_POST(self):
            # Local sandbox still rejects cross-site writes and DNS rebinding requests.
            allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed_hosts or self.headers.get("X-Worker-CSRF") != csrf:
                self.send({"error": "Invalid local session"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 8192:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected JSON object")
                path = urlparse(self.path).path
                if path == "/api/tasks":
                    task_id = runtime.create(body.get("request"), body.get("mode", "offline"), body.get("fault", "none"))
                    pool.submit(runtime.run, task_id)
                    self.send({"id": task_id}, 202)
                elif path.startswith("/api/tasks/") and path.endswith("/approval") and len(path.split("/")) == 5:
                    if type(body.get("approve")) is not bool:
                        raise ValueError("approve must be a boolean")
                    task_id = path.split("/")[3]
                    if runtime.decide(task_id, body["approve"]):
                        pool.submit(runtime.run, task_id)
                    self.send({"id": task_id})
                else:
                    self.send({"error": "Not found"}, 404)
            except (ValueError, TypeError) as exc:
                self.send({"error": str(exc)}, 400)
            except KeyError:
                self.send({"error": "Task not found"}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.pool = pool
    server.store = store
    for task_id in runtime.resume_on_startup():
        pool.submit(runtime.run, task_id)
    return server


if __name__ == "__main__":
    from worker.config import load_local_env
    load_local_env(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    args = parser.parse_args()
    server = make_server(args.data_dir, args.port)
    print(f"CentrAlign sandbox worker: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.pool.shutdown(wait=True)
