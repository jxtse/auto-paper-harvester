"""
Real-browser fallback: download PDFs through the Chrome you already use every day.

Why this exists
---------------
The Playwright fallback in :mod:`browser_fallback` launches its own Chromium profile.
That profile has no institutional login yet, and Cloudflare / Akamai / Radware bot walls
(ACS, MDPI, IOP, Taylor & Francis, ...) often keep it on a "Just a moment..." page forever.
Pointing Playwright at your real Chrome profile needs Chrome to be fully closed.

This module instead attaches to your *running* Chrome through the official
**Playwright Extension** (Chrome Web Store id ``mmlmfjhmonkocbjadbfplnigmagldckm``) and the
``playwright-cli`` tool. The requests then come from a browser that already holds your
institutional access and passes bot checks the normal way.

Two details matter in practice (both verified against ACS, MDPI and IEEE, 2026-10):

* The PDF is fetched *inside the page* with ``fetch(url, {credentials: 'include'})``.
  Playwright's node-side ``context.request`` shares cookies but not the browser's network
  fingerprint and still got a Cloudflare 403 on ACS.
* CDP auto-connect (``--cdp=chrome``, chrome-devtools-mcp ``--autoConnect``,
  agent-browser ``--auto-connect``) must read ``DevToolsActivePort`` from the Chrome
  profile. On macOS that directory is TCC-protected, so a background agent gets EPERM.
  The extension relay does not need that file when ``PLAYWRIGHT_MCP_EXECUTABLE_PATH``
  points at the Chrome binary.

Safety rules: one named session and one tab, every request goes through the
:class:`~auto_paper_download.ratelimit.Ledger` ``browser`` channel when a ledger is
configured, and a challenge page that does not clear on its own within ~40 s is never
clicked through: the publisher is put in cooldown and the result is ``challenge``.

Setup
-----
1. Install the Playwright Extension in Chrome and copy the token it shows.
2. ``npm i -g @playwright/cli`` (or set ``PLAYWRIGHT_CLI`` to the binary).
3. ``export PLAYWRIGHT_MCP_EXTENSION_TOKEN=...`` and run with
   ``--use-browser-fallback --browser-mode extension``.
"""

from __future__ import annotations

import base64
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .ratelimit import COOLDOWN_S, Ledger, LimitReached, block_signal

LOGGER = logging.getLogger(__name__)

CHALLENGE_RE = re.compile(
    r"just a moment|verify you are human|attention required|access denied|captcha|"
    r"are you a robot|请稍候|security check",
    re.I,
)

_DEFAULT_CHROME = {
    "Darwin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "Linux": "/usr/bin/google-chrome",
    "Windows": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
}

# Runs inside the publisher page. Finds the PDF link, fetches it with the page's own
# cookies and network stack, follows an HTML wrapper (IEEE stamp.jsp) to the embedded
# PDF, and returns the bytes base64-encoded through stdout.
PDF_JS = r"""async page => page.evaluate(async () => {
  const m = document.querySelector('meta[name="citation_pdf_url"]');
  let u = m ? m.content : '';
  if (!u) {
    const c = [...document.querySelectorAll('a[href]')].map(a => a.href)
      .filter(h => /pdf|stamp\.jsp/i.test(h) && !/suppl|_si_|supporting|epdf|citation|ris$/i.test(h));
    u = c[0] || '';
  }
  if (!u) return 'B64:0:none:AA==:END:';
  let r = await fetch(u, {credentials: 'include'});
  let a = new Uint8Array(await r.arrayBuffer());
  if (!(a[0] === 0x25 && a[1] === 0x50 && a[2] === 0x44 && a[3] === 0x46)) {
    const d = new DOMParser().parseFromString(new TextDecoder().decode(a), 'text/html');
    const f = [...d.querySelectorAll('iframe[src],embed[src],object[data]')]
      .map(e => e.getAttribute('src') || e.getAttribute('data'))
      .map(s => new URL(s, u).href).filter(s => /pdf/i.test(s))[0];
    if (f) { u = f; r = await fetch(u, {credentials: 'include'}); a = new Uint8Array(await r.arrayBuffer()); }
  }
  let s = '';
  for (let i = 0; i < a.length; i += 32768) s += String.fromCharCode.apply(null, a.subarray(i, i + 32768));
  return 'B64:' + r.status + ':' + (r.headers.get('content-type') || '').split(';')[0] + ':' + btoa(s) + ':END:' + u;
})"""

_RESULT_RE = re.compile(r"B64:(\d+):([^:]*):([A-Za-z0-9+/=]+):END:([^\"\\\n]*)")


@dataclass
class RealBrowserResult:
    doi: str
    status: str  # "ok" | "challenge" | "fail" | "rate_limited" | "unavailable" | "cached"
    saved_path: Optional[Path] = None
    reason: str = ""
    http: Optional[int] = None
    url: str = ""

    @property
    def success(self) -> bool:
        return self.status in ("ok", "cached")


def _slug(doi: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", doi.lower())


def parse_pdf_result(output: str) -> Optional[tuple[int, str, bytes, str]]:
    """Parse the ``B64:<status>:<content-type>:<base64>:END:<url>`` marker printed by :data:`PDF_JS`."""
    m = _RESULT_RE.search(output)
    if not m:
        return None
    return int(m.group(1)), m.group(2), base64.b64decode(m.group(3)), m.group(4)


class RealBrowser:
    """Drive the user's running Chrome through ``playwright-cli attach --extension``."""

    def __init__(self, *, session: Optional[str] = None, cli: Optional[str] = None,
                 ledger: Optional[Ledger] = None, settle_checks: int = 4, settle_s: float = 10.0):
        self.session = session or os.environ.get("PLAYWRIGHT_CLI_SESSION", "auto-paper")
        self.cli = cli or os.environ.get("PLAYWRIGHT_CLI") or shutil.which("playwright-cli")
        self.ledger = ledger
        self.settle_checks, self.settle_s = settle_checks, settle_s
        self._attached = False

    # -- plumbing ---------------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        if not self.cli:
            return False, "playwright-cli not found (npm i -g @playwright/cli, or set PLAYWRIGHT_CLI)"
        if not os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN"):
            return False, "PLAYWRIGHT_MCP_EXTENSION_TOKEN not set (copy it from the Playwright Extension)"
        return True, ""

    def _env(self) -> dict:
        env = dict(os.environ)
        exe = env.get("PLAYWRIGHT_MCP_EXECUTABLE_PATH") or _DEFAULT_CHROME.get(platform.system(), "")
        if exe:
            env["PLAYWRIGHT_MCP_EXECUTABLE_PATH"] = exe
        env.setdefault("PWTEST_EXTENSION_CONNECT_TIMEOUT", "60000")
        return env

    def _run(self, *args: str, timeout: float = 90) -> str:
        if not self.cli:
            raise RuntimeError("playwright-cli not found")
        p = subprocess.run([self.cli, f"-s={self.session}", *args], capture_output=True, text=True,
                           timeout=timeout, env=self._env())
        return p.stdout + p.stderr

    def attach(self) -> None:
        if self._attached or "Result" in self._run("eval", "1", timeout=30):
            self._attached = True
            return
        out = self._run("attach", "--extension=chrome", timeout=120)
        if "attached" not in out.lower():
            raise RuntimeError("could not attach to Chrome via the Playwright Extension: " + out[-300:])
        self._attached = True

    def detach(self) -> None:
        """Release the session. Never closes the user's browser."""
        if self._attached:
            self._run("detach", timeout=30)
            self._attached = False

    def _title(self) -> str:
        out = self._run("eval", "document.title", timeout=40)
        m = re.search(r'### Result\s*\n"?(.*?)"?\n', out)
        return m.group(1) if m else ""

    # -- public -----------------------------------------------------------------------
    def download(self, doi: str, output_dir: Path, *, overwrite: bool = False) -> RealBrowserResult:
        dest = Path(output_dir) / f"{_slug(doi)}.pdf"
        if dest.exists() and not overwrite:
            return RealBrowserResult(doi, "cached", dest)
        ok, why = self.available()
        if not ok:
            return RealBrowserResult(doi, "unavailable", reason=why)

        host = "browser:" + doi.split("/", 1)[0]  # pace per publisher (DOI prefix)
        rid: Optional[int] = None
        if self.ledger:
            try:
                rid = self.ledger.acquire("browser", host)
            except LimitReached as exc:
                return RealBrowserResult(doi, "rate_limited", reason=str(exc))

        def _fail(status: str, reason: str, http: Optional[int] = None, nbytes: int = 0,
                  cooldown: Optional[float] = None, url: str = "") -> RealBrowserResult:
            if self.ledger:
                if cooldown:
                    self.ledger.block(host, reason, seconds=cooldown)
                self.ledger.record("browser", host, doi, "fail", http, nbytes, reason, reservation=rid)
            return RealBrowserResult(doi, status, reason=reason, http=http, url=url)

        try:
            self.attach()
            self._run("goto", f"https://doi.org/{doi}", timeout=90)
            title = ""
            for _ in range(self.settle_checks):  # let a non-interactive challenge clear by itself
                time.sleep(self.settle_s)
                title = self._title()
                if title and not CHALLENGE_RE.search(title):
                    break
            if not title or CHALLENGE_RE.search(title):
                return _fail("challenge", f"challenge did not clear: {title[:80]}", cooldown=COOLDOWN_S)
            parsed = parse_pdf_result(self._run("run-code", PDF_JS, timeout=180))
        except (subprocess.TimeoutExpired, RuntimeError) as exc:
            return _fail("fail", f"{type(exc).__name__}: {exc}"[:300])
        if not parsed:
            return _fail("fail", "no PDF link or no result from page")
        status, ctype, body, url = parsed
        if status != 200 or body[:5] != b"%PDF-":
            # Only real block signals cool the publisher down. A plain 403 here is usually a
            # paywall / missing entitlement, which says nothing about the next DOI.
            cool = block_signal(status, body)
            return _fail("fail", f"HTTP {status} {ctype} (not a PDF)", http=status, nbytes=len(body),
                         cooldown=cool, url=url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        if self.ledger:
            self.ledger.record("browser", host, doi, "ok", status, len(body), url[:200], reservation=rid)
        return RealBrowserResult(doi, "ok", dest, http=status, url=url)
