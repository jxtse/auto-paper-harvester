"""CLI subcommands and the local web UI (no network: downloads are faked)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from auto_paper_download import __main__ as cli
from auto_paper_download import downloader, webui


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "ENV_FILE", tmp_path / "home" / ".env")
    monkeypatch.setattr(webui, "DEFAULT_LEDGER", tmp_path / "home" / "ledger.sqlite")
    for k in webui.FIELDS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class FakeStream:
    def __init__(self, paths, metrics):
        self._it, self.metrics = iter(paths), metrics

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._it)


def test_unresolved_dois_prefers_browser_outcome():
    metrics = {
        "Wiley": {"attempted": 2, "succeeded": 0,
                  "failed_dois": [{"doi": "10.1/a", "reason": "oa 403"},
                                  {"doi": "10.1/b", "reason": "oa 403"}]},
        "RealBrowser": {"attempted": 2, "succeeded": 1,
                        "failed_dois": [{"doi": "10.1/b", "reason": "challenge_timeout"}]},
    }
    assert downloader.unresolved_dois(metrics) == [{"doi": "10.1/b", "reason": "challenge_timeout"}]
    no_browser = {"Wiley": metrics["Wiley"]}
    assert [e["doi"] for e in downloader.unresolved_dois(no_browser)] == ["10.1/a", "10.1/b"]


def test_get_extracts_dois_and_prints_json(home, monkeypatch, capsys):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        out = kw["output_dir"] / "10.1016_j.x.1" / "10.1016_j.x.1.pdf"
        return FakeStream([out], {"Elsevier": {"attempted": 2, "succeeded": 1,
                                               "failed_dois": [{"doi": "10.1002/y.2", "reason": "no OA"}]}})

    monkeypatch.setattr(downloader, "download_from_dois", fake)
    refs = home / "refs.txt"
    refs.write_text("[1] Foo et al. https://doi.org/10.1002/y.2.\n")
    cli.main(["get", "10.1016/j.x.1", "--file", str(refs), "-o", str(home / "p"), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert seen["dois"] == ["10.1016/j.x.1", "10.1002/y.2"]
    assert seen["ledger"].path == webui.DEFAULT_LEDGER  # caps on by default
    assert out["requested"] == 2 and len(out["pdfs"]) == 1 and out["failed"][0]["doi"] == "10.1002/y.2"


def test_doi_extraction_strips_reference_punctuation():
    text = "Foo (doi:10.1002/x.1). Bar 10.1016/S0021-9258(19)52451-6, baz [10.5194/a-1-2]"
    assert downloader.extract_dois_from_text(text) == [
        "10.1002/x.1", "10.1016/S0021-9258(19)52451-6", "10.5194/a-1-2"]


def test_get_without_doi_exits(home):
    with pytest.raises(SystemExit, match="No DOI"):
        cli.main(["get", "hello"])


def test_doctor_reports_steps(home, monkeypatch, capsys):
    monkeypatch.setenv("OPENALEX_MAILTO", "a@b.c")
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "OK  1. open access: OpenAlex" in out
    assert "--  2. publisher TDM APIs: none" in out


def test_settings_file_is_private_and_secrets_not_echoed(home, monkeypatch):
    webui.write_env_file({"ELSEVIER_API_KEY": "sekret", "OPENALEX_MAILTO": "a@b.c", "EVIL": "x"})
    text = webui.ENV_FILE.read_text()
    assert "ELSEVIER_API_KEY=sekret" in text and "EVIL" not in text
    assert (webui.ENV_FILE.stat().st_mode & 0o777) == 0o600
    view = webui.settings_view()
    assert view["ELSEVIER_API_KEY"] == {"set": True, "value": "", "secret": True}
    assert view["OPENALEX_MAILTO"]["value"] == "a@b.c"
    webui.write_env_file({"ELSEVIER_API_KEY": ""})  # empty value removes the key
    assert "ELSEVIER_API_KEY" not in webui.ENV_FILE.read_text()


@pytest.fixture
def server(home, monkeypatch):
    app = webui.App(home / "l.sqlite", home / "out")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webui.make_handler(app, 0))
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = webui.make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, port
    httpd.shutdown()


def call(port, path, body=None, token=None, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Token", token)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_ui_requires_token_and_local_host(server):
    app, port = server
    assert call(port, "/api/state")[0] == 403
    assert call(port, "/api/state", token="wrong")[0] == 403
    assert call(port, "/api/state", token=app.token, host=f"evil.example:{port}")[0] == 403
    assert call(port, "/")[0] == 403
    status, html = call(port, f"/?t={app.token}")
    assert status == 200 and b"Auto Paper Harvester" in html
    status, body = call(port, "/api/state", token=app.token)
    state = json.loads(body)
    assert status == 200 and state["steps"]["oa"] == [] and state["job"] is None


def test_ui_download_job_runs(server, monkeypatch):
    app, port = server
    done = threading.Event()

    def fake(**kw):
        p = kw["output_dir"] / "10.5194_x" / "10.5194_x.pdf"
        done.set()
        return FakeStream([p], {"Crossref": {"attempted": 1, "succeeded": 1, "failed_dois": []}})

    monkeypatch.setattr(webui, "download_from_dois", fake)
    status, body = call(port, "/api/download", {"text": "see doi:10.5194/x", "output": ""}, token=app.token)
    job = json.loads(body)
    assert status == 200 and job["total"] == 1
    assert done.wait(5)
    view: dict = {}
    for _ in range(50):
        view = json.loads(call(port, f"/api/job/{job['id']}", token=app.token)[1])
        if view["state"] != "running":
            break
    assert view["state"] == "done" and view["saved"][0].endswith("10.5194_x.pdf")
    assert call(port, "/api/download", {"text": "nothing"}, token=app.token)[0] == 400
