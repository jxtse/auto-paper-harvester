import base64
import time

import pytest

from auto_paper_download import ratelimit
import multiprocessing as mp

import requests

from auto_paper_download.ratelimit import (
    HostLimited,
    challenge_page,
    Ledger,
    LimitReached,
    PacedAdapter,
    block_signal,
    block_signal_from_message,
)
from auto_paper_download.real_browser import RealBrowser, parse_pdf_result


def _ledger(tmp_path, **over):
    limits = {k: dict(v) for k, v in ratelimit.DEFAULT_LIMITS.items()}
    for ch, vals in over.items():
        limits[ch].update(vals)
    return Ledger(tmp_path / "ledger.sqlite", limits=limits)


def test_daily_success_cap(tmp_path):
    led = _ledger(tmp_path, browser=dict(daily_ok=2, gap_s=0, jitter_s=0))
    for i in range(2):
        rid = led.acquire("browser", "browser:10.1021")
        led.record("browser", "browser:10.1021", f"10.1021/x{i}", "ok", reservation=rid)
    with pytest.raises(LimitReached, match="browser daily download cap"):
        led.check("browser", "browser:10.1021")
    # other channels are unaffected
    led.check("oa", "oa:10.3390")


def test_attempt_cap_counts_failures(tmp_path):
    led = _ledger(tmp_path, inst=dict(daily_attempts=3, gap_s=0, jitter_s=0))
    for i in range(3):
        led.record("inst", f"h{i}", "d", "fail")
    with pytest.raises(LimitReached, match="attempt cap"):
        led.check("inst", "h9")


def test_per_host_cap(tmp_path):
    led = _ledger(tmp_path)
    led.per_host_daily = 2
    led.record("oa", "oa:10.1", "a", "fail")
    led.record("elsevier", "oa:10.1", "b", "fail")
    with pytest.raises(LimitReached, match="oa:10.1 daily attempt cap"):
        led.check("oa", "oa:10.1")


def test_global_cap(tmp_path):
    led = _ledger(tmp_path)
    led.global_daily_ok = 1
    led.record("oa", "a", "x", "ok")
    with pytest.raises(LimitReached, match="global"):
        led.check("wiley", "api:wiley")


def test_cooldown_blocks_and_expires(tmp_path):
    led = _ledger(tmp_path)
    led.block("www.mdpi.com", "Access Denied", seconds=3600)
    with pytest.raises(LimitReached, match="cooldown"):
        led.check("oa", "www.mdpi.com")
    led.block("www.mdpi.com", "old", seconds=-1)
    led.check("oa", "www.mdpi.com")


def test_host_gap_sleeps(tmp_path, monkeypatch):
    led = _ledger(tmp_path, wiley=dict(gap_s=8, jitter_s=0))
    slept = []
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: slept.append(s))
    led.record("wiley", "api:wiley", "a", "ok")
    led.acquire("wiley", "api:wiley")
    assert slept and 7 < slept[0] <= 8


def test_persists_across_instances(tmp_path):
    a = _ledger(tmp_path, browser=dict(daily_ok=1))
    a.record("browser", "h", "d", "ok")
    b = _ledger(tmp_path, browser=dict(daily_ok=1))
    with pytest.raises(LimitReached):
        b.check("browser", "h2")


def test_env_override(monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_LIMIT_BROWSER", "3,5,10,1")
    lim = ratelimit._env_limits(ratelimit.DEFAULT_LIMITS)["browser"]
    assert (lim["daily_ok"], lim["daily_attempts"], lim["gap_s"], lim["jitter_s"]) == (3, 5, 10, 1)


@pytest.mark.parametrize(
    "status,body,expect",
    [
        (429, b"", ratelimit.COOLDOWN_429_S),
        (403, b"<title>Access Denied</title> Reference #18", ratelimit.COOLDOWN_S),
        (200, b"<title>Just a moment...</title>", ratelimit.COOLDOWN_S),
        (403, b"<p>You do not have a subscription</p>", None),  # paywall, not a ban
        (200, b"%PDF-1.7", None),
    ],
)
def test_block_signal(status, body, expect):
    assert block_signal(status, body) == expect


def test_block_signal_from_message():
    assert block_signal_from_message("HTTPError: 429 Too Many Requests") == ratelimit.COOLDOWN_429_S
    assert block_signal_from_message("DownloadError: no PDF link") is None


def test_parse_pdf_result():
    pdf = b"%PDF-1.4 hello"
    out = f'### Result\n"B64:200:application/pdf:{base64.b64encode(pdf).decode()}:END:https://x.org/a.pdf"\n'
    status, ctype, body, url = parse_pdf_result(out)
    assert (status, ctype, body, url) == (200, "application/pdf", pdf, "https://x.org/a.pdf")
    assert parse_pdf_result("Error: not attached") is None


def test_real_browser_unavailable_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_MCP_EXTENSION_TOKEN", raising=False)
    rb = RealBrowser(cli="/bin/true")
    res = rb.download("10.1021/acs.x", tmp_path)
    assert res.status == "unavailable" and "TOKEN" in res.reason


def test_real_browser_rate_limited_before_any_request(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "x")
    led = _ledger(tmp_path, browser=dict(daily_ok=0))
    rb = RealBrowser(cli="/bin/false", ledger=led)
    calls = []
    monkeypatch.setattr(rb, "_run", lambda *a, **k: calls.append(a) or "")
    res = rb.download("10.1021/acs.x", tmp_path)
    assert res.status == "rate_limited" and calls == []


def test_real_browser_challenge_sets_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "x")
    led = _ledger(tmp_path, browser=dict(gap_s=0, jitter_s=0))
    rb = RealBrowser(cli="/bin/false", ledger=led, settle_checks=2, settle_s=0)

    def fake_run(*args, **kw):
        if args[:2] == ("eval", "1"):
            return "### Result\n1\n"
        if args[:2] == ("eval", "document.title"):
            return '### Result\n"Just a moment..."\n'
        return ""

    monkeypatch.setattr(rb, "_run", fake_run)
    res = rb.download("10.1021/acs.x", tmp_path)
    assert res.status == "challenge"
    with pytest.raises(LimitReached, match="cooldown"):
        led.check("browser", "browser:10.1021")


def test_real_browser_rejects_html_as_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "x")
    led = _ledger(tmp_path, browser=dict(gap_s=0, jitter_s=0))
    rb = RealBrowser(cli="/bin/false", ledger=led, settle_checks=1, settle_s=0)
    html = base64.b64encode(b"<!DOCTYPE html>").decode()

    def fake_run(*args, **kw):
        if args[0] == "eval" and args[1] == "1":
            return "### Result\n1\n"
        if args[0] == "eval":
            return '### Result\n"Some article - Publisher"\n'
        if args[0] == "run-code":
            return f'### Result\n"B64:200:text/html:{html}:END:https://p.org/pdf"\n'
        return ""

    monkeypatch.setattr(rb, "_run", fake_run)
    res = rb.download("10.1088/x", tmp_path)
    assert res.status == "fail" and "not a PDF" in res.reason
    assert not (tmp_path / "10.1088_x.pdf").exists()
    assert led.usage()["browser"]["attempts"] == 1 and led.usage()["browser"]["ok"] == 0


# --- review findings on PR #7 -------------------------------------------------------


def test_pending_reservation_counts_against_success_cap(tmp_path):
    led = _ledger(tmp_path, browser=dict(daily_ok=1, gap_s=0, jitter_s=0))
    led.acquire("browser", "h")  # in flight, not finalized yet
    with pytest.raises(LimitReached, match="download cap"):
        led.acquire("browser", "h2")


def test_failed_reservation_releases_success_slot(tmp_path):
    led = _ledger(tmp_path, browser=dict(daily_ok=1, gap_s=0, jitter_s=0))
    rid = led.acquire("browser", "h")
    led.record("browser", "h", "d", "fail", reservation=rid)
    led.acquire("browser", "h2")  # does not raise
    assert led.usage()["browser"]["attempts"] == 2


def _worker(path, q):
    led = Ledger(path, limits={**{k: dict(v) for k, v in ratelimit.DEFAULT_LIMITS.items()},
                               "browser": dict(daily_ok=1, daily_attempts=50, gap_s=0, jitter_s=0)})
    try:
        led.acquire("browser", "h")
        q.put("got")
    except LimitReached:
        q.put("refused")


def test_concurrent_processes_cannot_both_take_last_slot(tmp_path):
    path = tmp_path / "l.sqlite"
    Ledger(path)  # create schema
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_worker, args=(path, q)) for _ in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    got = sorted(q.get(timeout=5) for _ in procs)
    assert got.count("got") == 1 and got.count("refused") == 5


def test_concurrent_slots_are_spaced_by_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: None)
    a = _ledger(tmp_path, browser=dict(gap_s=60, jitter_s=0))
    b = _ledger(tmp_path, browser=dict(gap_s=60, jitter_s=0))
    a.acquire("browser", "h")
    b.acquire("browser", "h")
    ts = [row[0] for row in a.db.execute("select ts from attempts order by ts")]
    assert ts[1] - ts[0] >= 59.9


def test_extension_plain_403_does_not_cool_down(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "x")
    led = _ledger(tmp_path, browser=dict(gap_s=0, jitter_s=0))
    rb = RealBrowser(cli="/bin/false", ledger=led, settle_checks=1, settle_s=0)
    page = base64.b64encode(b"<html><p>Purchase this article. You do not have access.</p></html>").decode()

    def fake_run(*args, **kw):
        if args[0] == "eval" and args[1] == "1":
            return "### Result\n1\n"
        if args[0] == "eval":
            return '### Result\n"Some article - Publisher"\n'
        if args[0] == "run-code":
            return f'### Result\n"B64:403:text/html:{page}:END:https://p.org/pdf"\n'
        return ""

    monkeypatch.setattr(rb, "_run", fake_run)
    assert rb.download("10.1088/a", tmp_path).status == "fail"
    assert led.cooling("browser:10.1088") is None
    led.check("browser", "browser:10.1088")


def test_extension_akamai_403_cools_down(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "x")
    led = _ledger(tmp_path, browser=dict(gap_s=0, jitter_s=0))
    rb = RealBrowser(cli="/bin/false", ledger=led, settle_checks=1, settle_s=0)
    page = base64.b64encode(b"<title>Access Denied</title> Reference #18.abc").decode()

    def fake_run(*args, **kw):
        if args[0] == "eval" and args[1] == "1":
            return "### Result\n1\n"
        if args[0] == "eval":
            return '### Result\n"Some article"\n'
        return f'### Result\n"B64:403:text/html:{page}:END:https://p.org/pdf"\n'

    monkeypatch.setattr(rb, "_run", fake_run)
    rb.download("10.3390/a", tmp_path)
    assert led.cooling("browser:10.3390")


def _session_with(led, monkeypatch, status=200, body=b"{}"):
    sent = []

    def fake_send(self, request, **kw):
        sent.append(request.url)
        resp = requests.Response()
        resp.status_code, resp._content, resp.url = status, body, request.url
        resp.request = request
        return resp

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    s = requests.Session()
    ratelimit.install(led, [s], channel="oa")
    return s, sent


def test_adapter_paces_by_real_host_across_doi_prefixes(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: slept.append(s))
    led = _ledger(tmp_path, oa=dict(gap_s=5, jitter_s=0))
    s, sent = _session_with(led, monkeypatch)
    s.get("https://api.openalex.org/works/doi:10.1021/a")
    s.get("https://api.openalex.org/works/doi:10.1039/b")  # different prefix, same host
    hosts = [r[0] for r in led.db.execute("select host from requests")]
    assert hosts == ["api.openalex.org", "api.openalex.org"]
    assert slept and slept[0] > 0  # second call had to wait for the shared host slot


def test_adapter_publisher_host_daily_cap_and_metadata_exempt(tmp_path, monkeypatch):
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: None)
    led = _ledger(tmp_path)
    led.per_host_daily_requests = 2
    s, _ = _session_with(led, monkeypatch)
    s.get("https://pubs.example.org/a.pdf")
    s.get("https://pubs.example.org/b.pdf")
    with pytest.raises(HostLimited, match="pubs.example.org daily request cap"):
        s.get("https://pubs.example.org/c.pdf")
    for i in range(5):  # metadata APIs are not capped per host
        s.get(f"https://api.openalex.org/works/{i}")


def test_adapter_block_signal_cools_real_host(tmp_path, monkeypatch):
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: None)
    led = _ledger(tmp_path)
    s, _ = _session_with(led, monkeypatch, status=403, body=b"<title>Access Denied</title>")
    s.get("https://www.mdpi.com/x/pdf")
    assert led.cooling("www.mdpi.com")
    with pytest.raises(HostLimited, match="cooldown"):
        s.get("https://www.mdpi.com/y/pdf")


def test_adapter_plain_403_and_429(tmp_path, monkeypatch):
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: None)
    led = _ledger(tmp_path)
    s, _ = _session_with(led, monkeypatch, status=403, body=b"<p>no subscription</p>")
    s.get("https://pubs.example.org/x.pdf")
    assert led.cooling("pubs.example.org") is None
    s2, _ = _session_with(led, monkeypatch, status=429, body=b"")
    s2.get("https://api.example.com/x")
    assert led.cooling("api.example.com")


def test_adapter_200_challenge_page_refused_and_cools_down(tmp_path, monkeypatch):
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: None)
    led = _ledger(tmp_path)
    body = b'<!DOCTYPE html><html><head><title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/x.js">'
    sent = []

    def fake_send(self, request, **kw):
        sent.append(request.url)
        resp = requests.Response()
        resp.status_code, resp._content, resp.url = 200, body, request.url
        resp.headers["Content-Type"] = "text/html; charset=UTF-8"
        resp.request = request
        return resp

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    s = requests.Session()
    ratelimit.install(led, [s], channel="oa")
    with pytest.raises(HostLimited, match="challenge page"):
        s.get("https://pubs.example.org/doi/pdf/10.1/x")
    assert led.cooling("pubs.example.org")
    with pytest.raises(HostLimited, match="cooldown"):
        s.get("https://pubs.example.org/doi/pdf/10.1/y")
    assert len(sent) == 1  # nothing sent while cooling down


def test_challenge_page_ignores_article_with_recaptcha_widget():
    article = (b"<html><head><title>Inverse problems in ODEs | Journal</title>"
               b'<script src="https://www.google.com/recaptcha/api.js"></script></head><body>...')
    assert not challenge_page(article)
    assert challenge_page(b"<html><head><title>Attention Required! | Cloudflare</title>")
    assert challenge_page(b"<html><head><TITLE>Access Denied</TITLE></head>")


def test_200_pdf_not_inspected(tmp_path, monkeypatch):
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: None)
    led = _ledger(tmp_path)

    def fake_send(self, request, **kw):
        resp = requests.Response()
        resp.status_code, resp._content, resp.url = 200, b"%PDF-1.7 ... just a moment", request.url
        resp.headers["Content-Type"] = "application/pdf"
        resp.request = request
        return resp

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    s = requests.Session()
    ratelimit.install(led, [s], channel="oa")
    assert s.get("https://pubs.example.org/a.pdf").content.startswith(b"%PDF")
    assert led.cooling("pubs.example.org") is None


def test_env_overrides_read_at_construction_not_import(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_PAPER_GLOBAL_DAILY_OK", "7")
    monkeypatch.setenv("AUTO_PAPER_PER_HOST_DAILY_REQUESTS", "9")
    monkeypatch.setenv("AUTO_PAPER_LIMIT_BROWSER", "3,5,10,1")
    led = Ledger(tmp_path / "e.sqlite")
    assert led.global_daily_ok == 7 and led.per_host_daily_requests == 9
    assert led.limits["browser"]["daily_ok"] == 3


def test_cli_loads_dotenv_before_ledger(tmp_path, monkeypatch):
    from auto_paper_download import __main__ as cli

    (tmp_path / ".env").write_text("AUTO_PAPER_GLOBAL_DAILY_OK=5\nAUTO_PAPER_LIMIT_OA=2,4,1,0\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTO_PAPER_GLOBAL_DAILY_OK", raising=False)
    monkeypatch.delenv("AUTO_PAPER_LIMIT_OA", raising=False)
    (tmp_path / "savedrecs.xls").write_text("")
    seen = {}

    def fake_download(**kw):
        seen["ledger"] = kw["ledger"]
        return iter(())

    monkeypatch.setattr(cli, "download_from_savedrecs", fake_download)
    try:
        cli.main(["--savedrecs", str(tmp_path / "savedrecs.xls"), "--ledger", str(tmp_path / "l.sqlite"),
                  "--output-dir", str(tmp_path / "out")])
    except SystemExit:
        pass
    assert seen["ledger"].global_daily_ok == 5
    assert seen["ledger"].limits["oa"]["daily_ok"] == 2
