"""Local web UI: configure keys, connect your browser, and download papers.

Run ``auto-paper-download ui``. The server listens on 127.0.0.1 only and every API call
must carry the random token that is part of the URL it prints, so other websites open in
your browser cannot use it.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .downloader import download_from_dois, extract_dois_from_text, load_env_file, unresolved_dois
from .ratelimit import Ledger

LOGGER = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("AUTO_PAPER_HOME", "~/.auto-paper-download")).expanduser()
ENV_FILE = CONFIG_DIR / ".env"
DEFAULT_LEDGER = CONFIG_DIR / "ledger.sqlite"
DEFAULT_OUTPUT = Path("~/Downloads/papers").expanduser()

# Settings the UI may read and write. Secrets are never sent back to the page, only
# whether they are set.
FIELDS = {
    "OPENALEX_MAILTO": False,
    "UNPAYWALL_EMAIL": False,
    "CROSSREF_MAILTO": False,
    "ELSEVIER_API_KEY": True,
    "WILEY_TDM_TOKEN": True,
    "SPRINGER_API_KEY": True,
    "PLAYWRIGHT_MCP_EXTENSION_TOKEN": True,
    "PLAYWRIGHT_CLI": False,
    "AUTO_PAPER_LEDGER": False,
}
EXTENSION_URL = ("https://chromewebstore.google.com/detail/playwright-extension/"
                 "mmlmfjhmonkocbjadbfplnigmagldckm")


# --- settings ------------------------------------------------------------------------

def read_env_file(path: Optional[Path] = None) -> dict[str, str]:
    path = path or ENV_FILE
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    return out


def write_env_file(values: dict[str, str], path: Optional[Path] = None) -> None:
    path = path or ENV_FILE
    current = read_env_file(path)
    for key, value in values.items():
        if key not in FIELDS:
            continue
        value = (value or "").strip()
        if value:
            current[key] = value
        else:
            current.pop(key, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# Written by `auto-paper-download ui`. Keep this file private.\n"
    body += "".join(f"{k}={v}\n" for k, v in sorted(current.items()))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    for key in values:
        if key in FIELDS:
            if current.get(key):
                os.environ[key] = current[key]
            else:
                os.environ.pop(key, None)


def settings_view() -> dict:
    view = {}
    for key, secret in FIELDS.items():
        value = os.environ.get(key, "")
        view[key] = {"set": bool(value), "value": "" if secret else value, "secret": secret}
    return view


def steps_view() -> dict:
    env = os.environ
    oa = [n for n, k in (("OpenAlex", "OPENALEX_MAILTO"), ("Unpaywall", "UNPAYWALL_EMAIL"),
                         ("Crossref", "CROSSREF_MAILTO")) if env.get(k)]
    tdm = [n for n, k in (("Elsevier", "ELSEVIER_API_KEY"), ("Wiley", "WILEY_TDM_TOKEN"),
                          ("Springer", "SPRINGER_API_KEY")) if env.get(k)]
    from .real_browser import RealBrowser

    ok, why = RealBrowser().available()
    return {"oa": oa, "tdm": tdm, "browser": {"ready": ok, "why": why},
            "extension_url": EXTENSION_URL}


def ledger_view(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "usage": None}
    usage = Ledger(path).usage()
    return {"path": str(path), "usage": usage}


# --- download jobs -------------------------------------------------------------------

class Job:
    def __init__(self, dois: list[str], output: Path, browser: Optional[str], ledger: Path):
        self.id = secrets.token_hex(4)
        self.dois, self.output, self.browser, self.ledger_path = dois, output, browser, ledger
        self.saved: list[str] = []
        self.log: list[str] = []
        self.metrics: dict = {}
        self.state = "running"
        self.started = time.time()
        self.cancel = threading.Event()

    def view(self) -> dict:
        failed = unresolved_dois(self.metrics)
        return {"id": self.id, "state": self.state, "total": len(self.dois),
                "saved": self.saved, "failed": failed, "log": self.log[-200:],
                "output": str(self.output)}

    def run(self) -> None:
        handler = _ListHandler(self.log)
        root = logging.getLogger("auto_paper_download")
        root.addHandler(handler)
        try:
            stream = download_from_dois(
                dois=self.dois, output_dir=self.output, load_env=False,
                use_browser_fallback=bool(self.browser), browser_mode=self.browser or "playwright",
                ledger=Ledger(self.ledger_path))
            for path in stream:
                self.saved.append(str(path))
                self.metrics = getattr(stream, "metrics", {}) or {}
                if self.cancel.is_set():
                    self.state = "cancelled"
                    break
            self.metrics = getattr(stream, "metrics", {}) or self.metrics
            if self.state == "running":
                self.state = "done"
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("download job failed")
            self.log.append(f"ERROR {type(exc).__name__}: {exc}")
            self.state = "error"
        finally:
            root.removeHandler(handler)


class _ListHandler(logging.Handler):
    def __init__(self, sink: list[str]):
        super().__init__(logging.INFO)
        self.sink = sink
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(self.format(record))


# --- HTTP ----------------------------------------------------------------------------

class App:
    def __init__(self, ledger: Path, output: Path):
        self.token = secrets.token_urlsafe(18)
        self.ledger, self.output = ledger, output
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def running_job(self) -> Optional[Job]:
        return next((j for j in self.jobs.values() if j.state == "running"), None)


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "auto-paper-download"

        def log_message(self, format, *args):  # noqa: A002 - quiet access log
            LOGGER.debug("ui %s", format % args)

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _guard(self) -> bool:
            # Host check blocks DNS rebinding; the token blocks other websites (CSRF).
            if self.headers.get("Host") not in allowed_hosts:
                self._json({"error": "bad host"}, HTTPStatus.FORBIDDEN)
                return False
            if self.path.startswith("/api/") and \
                    not secrets.compare_digest(self.headers.get("X-Token", ""), app.token):
                self._json({"error": "bad token"}, HTTPStatus.FORBIDDEN)
                return False
            return True

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):  # noqa: N802
            if not self._guard():
                return
            url = urlparse(self.path)
            if url.path == "/":
                if parse_qs(url.query).get("t", [""])[0] != app.token:
                    self._send(403, b"Open the URL printed in the terminal.", "text/plain")
                    return
                html = resources.files(__package__).joinpath("ui/index.html").read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
            elif url.path == "/api/state":
                job = app.running_job() or _last(app.jobs)
                self._json({"settings": settings_view(), "steps": steps_view(),
                            "ledger": ledger_view(app.ledger), "output": str(app.output),
                            "env_file": str(ENV_FILE), "job": job.view() if job else None})
            elif url.path.startswith("/api/job/"):
                job = app.jobs.get(url.path.rsplit("/", 1)[-1])
                self._json(job.view() if job else {"error": "no such job"}, 200 if job else 404)
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            if not self._guard():
                return
            try:
                data = self._body()
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": str(exc)}, 400)
                return
            path = urlparse(self.path).path
            if path == "/api/settings":
                write_env_file({k: v for k, v in data.items() if isinstance(v, str)})
                self._json({"ok": True, "settings": settings_view(), "steps": steps_view()})
            elif path == "/api/browser/test":
                from .real_browser import RealBrowser

                rb = RealBrowser()
                ok, why = rb.available()
                if ok:
                    try:
                        rb.attach()
                        rb.detach()
                    except Exception as exc:  # noqa: BLE001
                        ok, why = False, str(exc)[-300:]
                self._json({"ok": ok, "why": why})
            elif path == "/api/download":
                with app.lock:
                    if app.running_job():
                        self._json({"error": "a download is already running"}, 409)
                        return
                    dois = extract_dois_from_text(str(data.get("text", "")))
                    if not dois:
                        self._json({"error": "no DOI found in the text"}, 400)
                        return
                    if len(dois) > 500:
                        self._json({"error": "at most 500 DOIs per run"}, 400)
                        return
                    browser = data.get("browser") or None
                    if browser not in (None, "extension", "playwright"):
                        self._json({"error": "bad browser mode"}, 400)
                        return
                    output = Path(str(data.get("output") or app.output)).expanduser()
                    job = Job(dois, output, browser, app.ledger)
                    app.jobs[job.id] = job
                threading.Thread(target=job.run, daemon=True).start()
                self._json(job.view())
            elif path.startswith("/api/job/") and path.endswith("/cancel"):
                job = app.jobs.get(path.split("/")[3])
                if job:
                    job.cancel.set()
                self._json({"ok": bool(job)})
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def _last(jobs: dict[str, Job]) -> Optional[Job]:
    return max(jobs.values(), key=lambda j: j.started) if jobs else None


def serve(*, port: int = 8765, open_browser: bool = True, ledger: Optional[Path] = None,
          output: Optional[Path] = None) -> None:
    load_env_file(".env")
    load_env_file(ENV_FILE)
    ledger = Path(ledger or os.environ.get("AUTO_PAPER_LEDGER") or DEFAULT_LEDGER).expanduser()
    app = App(ledger, Path(output or DEFAULT_OUTPUT).expanduser())
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    url = f"http://127.0.0.1:{port}/?t={app.token}"
    print(f"auto-paper-download UI: {url}\n(settings: {ENV_FILE}; Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
