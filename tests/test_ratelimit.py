import base64
import time

import pytest

from auto_paper_download import ratelimit
from auto_paper_download.ratelimit import Ledger, LimitReached, block_signal, block_signal_from_message
from auto_paper_download.real_browser import RealBrowser, parse_pdf_result


def _ledger(tmp_path, **over):
    limits = {k: dict(v) for k, v in ratelimit.DEFAULT_LIMITS.items()}
    for ch, vals in over.items():
        limits[ch].update(vals)
    return Ledger(tmp_path / "ledger.sqlite", limits=limits)


def test_daily_success_cap(tmp_path):
    led = _ledger(tmp_path, browser=dict(daily_ok=2, gap_s=0, jitter_s=0))
    for i in range(2):
        led.acquire("browser", "browser:10.1021")
        led.record("browser", "browser:10.1021", f"10.1021/x{i}", "ok")
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
