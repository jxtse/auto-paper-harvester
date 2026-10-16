"""
Persistent download ledger: daily caps, per-host pacing and block cooldowns.

Why this exists
---------------
Publishers ban by IP. A per-run ``--delay`` does not protect you when the same machine
runs the tool several times a day, or when a bot wall (Cloudflare / Akamai / Radware)
starts answering with 403/429 and the run keeps hammering it. The ledger keeps state in
SQLite so the limits hold across runs and processes:

1. **Daily caps** per channel (successful downloads AND attempts) plus a global
   successful-download cap, counted per local calendar day.
2. **Per-host spacing**: a minimum gap (+ random jitter) between two HTTP requests to
   the same host (the real host of every request, redirects included), whatever process
   sent the previous one. See :class:`PacedAdapter`.
3. **Cooldowns**: after a block signal (HTTP 429, CAPTCHA / challenge page, Akamai
   "Access Denied") the host is refused for 6-24 h instead of retried.

Enable it with ``--ledger PATH`` (CLI) or ``AUTO_PAPER_LEDGER=PATH``. Without a ledger
the downloader behaves exactly as before (fixed ``--delay`` only).

The defaults are deliberately conservative; they assume you screen papers first and
only download the few you actually need. Override per channel with environment
variables, e.g. ``AUTO_PAPER_LIMIT_BROWSER=12,20,60,60`` (ok/day, attempts/day,
gap seconds, jitter seconds).
"""

from __future__ import annotations

import os
import random
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from requests.adapters import HTTPAdapter

# channel -> daily successful downloads, daily attempts, min seconds between two requests
# to one host, extra random jitter seconds.
DEFAULT_LIMITS: dict[str, dict[str, float]] = {
    "oa":       dict(daily_ok=40, daily_attempts=80, gap_s=4.0, jitter_s=3.0),    # OpenAlex/Unpaywall/Crossref OA URLs
    "elsevier": dict(daily_ok=30, daily_attempts=45, gap_s=3.0, jitter_s=2.0),    # api.elsevier.com
    "wiley":    dict(daily_ok=30, daily_attempts=45, gap_s=8.0, jitter_s=4.0),    # api.wiley.com (hard cap 3 req/s)
    "springer": dict(daily_ok=30, daily_attempts=45, gap_s=4.0, jitter_s=3.0),    # Springer OA API
    "inst":     dict(daily_ok=15, daily_attempts=25, gap_s=45.0, jitter_s=45.0),  # plain HTTP on an institutional IP
    "browser":  dict(daily_ok=12, daily_attempts=20, gap_s=60.0, jitter_s=60.0),  # real browser session
}
GLOBAL_DAILY_OK = 80          # all channels together
PER_HOST_DAILY_ATTEMPTS = 25    # browser attempts per publisher (DOI prefix) per day
PER_HOST_DAILY_REQUESTS = 100   # HTTP requests to one publisher host per day (one paper
                                # with supplements can take ~10 requests)
COOLDOWN_S = 24 * 3600        # after a bot-wall signal
COOLDOWN_429_S = 6 * 3600     # after HTTP 429
PENDING_TTL_S = 3600          # a reservation older than this is treated as a crashed run

# Metadata / resolver APIs shared by every channel. They are paced lightly (they publish
# their own generous limits) and are exempt from the per-host daily cap; their use is
# bounded by the per-channel paper caps instead.
METADATA_HOSTS = {"api.openalex.org", "api.crossref.org", "api.unpaywall.org", "doi.org", "dx.doi.org"}
METADATA_GAP_S, METADATA_JITTER_S = 1.0, 1.0
# Publisher TDM APIs: paced with the channel gap, exempt from the per-host daily cap.
VENDOR_API_HOSTS = {"api.elsevier.com", "api.wiley.com", "api.springernature.com"}

_BLOCK_PATTERNS = (b"captcha", b"cf-chl", b"just a moment", b"verify you are human",
                   b"attention required", b"are you a robot")


class LimitReached(RuntimeError):
    """Raised when a request is not allowed right now (cap reached or host cooling down)."""


class HostLimited(LimitReached):
    """One HTTP host is cooling down or at its daily cap; other routes (e.g. a browser) may still work."""


def block_signal(status: Optional[int], body: bytes = b"") -> Optional[float]:
    """Return a cooldown length in seconds if a response looks like a block, else ``None``.

    429 -> 6 h. CAPTCHA / challenge pages and Akamai-style "403 Access Denied" -> 24 h.
    A plain 403 on its own is usually "no subscription", not a ban, so it is not a signal.
    """
    if status == 429:
        return COOLDOWN_429_S
    low = (body or b"")[:20000].lower()
    if any(p in low for p in _BLOCK_PATTERNS):
        return COOLDOWN_S
    if status == 403 and b"access denied" in low:
        return COOLDOWN_S
    return None


def block_signal_from_message(message: str) -> Optional[float]:
    """Same as :func:`block_signal` for clients that only surface an error string."""
    if re.search(r"\b429\b", message):
        return COOLDOWN_429_S
    return block_signal(None, message.encode("utf-8", "ignore"))


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


# Challenge pages served with HTTP 200 (Cloudflare, Akamai, PerimeterX, ...). Matched on the
# <title> or on challenge-script markers only, because ordinary article pages often embed a
# reCAPTCHA widget and must not trigger a cooldown.
_CHALLENGE_TITLE = re.compile(
    rb"<title[^>]*>\s*(just a moment|attention required|access denied|are you a robot|"
    rb"verify you are human|please verify|security check|human verification|captcha)",
    re.I,
)
_CHALLENGE_MARKERS = (b"cf-chl", b"cf_chl_opt", b"_cf_chl", b"challenge-platform", b"px-captcha",
                      b"/cdn-cgi/challenge")


def challenge_page(body: bytes) -> bool:
    """True if an HTML body (any status) is a bot-wall challenge rather than content."""
    head = (body or b"")[:20000]
    return bool(_CHALLENGE_TITLE.search(head)) or any(m in head.lower() for m in _CHALLENGE_MARKERS)


def _env_limits(limits: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    out = {k: dict(v) for k, v in limits.items()}
    for ch in list(out):
        raw = os.environ.get(f"AUTO_PAPER_LIMIT_{ch.upper()}")
        if not raw:
            continue
        parts = [float(x) for x in raw.split(",")]
        for key, val in zip(("daily_ok", "daily_attempts", "gap_s", "jitter_s"), parts):
            out[ch][key] = val
    return out


@dataclass
class Ledger:
    """SQLite-backed rate limiter, safe to share between processes.

    Every check-and-reserve runs inside one ``BEGIN IMMEDIATE`` transaction, so two
    processes cannot both take the last slot of a cap or the same per-host time slot.
    """

    path: Path
    limits: dict = field(default_factory=lambda: _env_limits(DEFAULT_LIMITS))
    # default_factory, not a plain default: read the environment when the Ledger is built
    # (after .env is loaded), not when this module is imported.
    global_daily_ok: int = field(default_factory=lambda: _env_int(
        "AUTO_PAPER_GLOBAL_DAILY_OK", GLOBAL_DAILY_OK))
    per_host_daily: int = field(default_factory=lambda: _env_int(
        "AUTO_PAPER_PER_HOST_DAILY", PER_HOST_DAILY_ATTEMPTS))
    per_host_daily_requests: int = field(default_factory=lambda: _env_int(
        "AUTO_PAPER_PER_HOST_DAILY_REQUESTS", PER_HOST_DAILY_REQUESTS))

    def __post_init__(self) -> None:
        self.path = Path(self.path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit mode; write transactions are opened explicitly with BEGIN IMMEDIATE.
        self.db = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        self.db.executescript(
            """
            create table if not exists attempts(ts real, day text, channel text, host text, doi text,
                                                status text, http int, bytes int, note text);
            create table if not exists cooldown(host text primary key, until real, reason text);
            create table if not exists requests(ts real, day text, host text, channel text);
            create table if not exists hosts(host text primary key, next_ts real);
            create index if not exists a_day on attempts(day, channel);
            create index if not exists r_day on requests(day, host);
            """
        )

    @classmethod
    def from_env(cls) -> Optional["Ledger"]:
        path = os.environ.get("AUTO_PAPER_LEDGER")
        return cls(Path(path)) if path else None

    @staticmethod
    def today() -> str:
        return time.strftime("%Y-%m-%d")

    # -- helpers -------------------------------------------------------------------------
    def _tx(self):
        db = self.db

        class _T:
            def __enter__(self_inner):
                db.execute("begin immediate")
                return db

            def __exit__(self_inner, exc_type, *_):
                db.execute("rollback" if exc_type else "commit")
                return False

        return _T()

    def _count(self, where: str, args: tuple) -> int:
        return self.db.execute(
            f"select count(*) from attempts where day=? and {where}", (self.today(), *args)
        ).fetchone()[0]

    def _ok_or_pending(self, where: str, args: tuple) -> int:
        # A live reservation might still succeed, so it counts against success caps.
        return self._count(
            f"(status='ok' or (status='pending' and ts>?)) and {where}",
            (time.time() - PENDING_TTL_S, *args),
        )

    def _lim(self, channel: str) -> dict[str, float]:
        return self.limits.get(channel) or self.limits["oa"]

    def usage(self) -> dict:
        out: dict = {}
        for ch, lim in self.limits.items():
            out[ch] = dict(ok=self._count("channel=? and status='ok'", (ch,)),
                           attempts=self._count("channel=?", (ch,)),
                           cap_ok=lim["daily_ok"], cap_attempts=lim["daily_attempts"])
        out["_global_ok"] = self._count("status='ok'", ())
        out["_cooldown"] = [
            dict(host=h, until=time.strftime("%F %T", time.localtime(u)), reason=r)
            for h, u, r in self.db.execute(
                "select host, until, reason from cooldown where until>?", (time.time(),))
        ]
        return out

    def cooling(self, host: str) -> Optional[str]:
        row = self.db.execute("select until, reason from cooldown where host=?", (host,)).fetchone()
        if row and row[0] > time.time():
            return f"{host} in cooldown until {time.strftime('%F %T', time.localtime(row[0]))}: {row[1]}"
        return None

    def _check(self, channel: str, host: Optional[str]) -> None:
        lim = self._lim(channel)
        if host:
            msg = self.cooling(host)
            if msg:
                raise LimitReached(msg)
        if self._ok_or_pending("1", ()) >= self.global_daily_ok:
            raise LimitReached(f"global daily cap {self.global_daily_ok} reached")
        if self._ok_or_pending("channel=?", (channel,)) >= lim["daily_ok"]:
            raise LimitReached(f"{channel} daily download cap {int(lim['daily_ok'])} reached")
        if self._count("channel=?", (channel,)) >= lim["daily_attempts"]:
            raise LimitReached(f"{channel} daily attempt cap {int(lim['daily_attempts'])} reached")
        if host and self._count("host=?", (host,)) >= self.per_host_daily:
            raise LimitReached(f"{host} daily attempt cap {self.per_host_daily} reached")

    def _reserve_slot(self, host: str, gap: float, jitter: float) -> float:
        """Inside a transaction: return the earliest allowed start time and book the next one."""
        now = time.time()
        row = self.db.execute("select next_ts from hosts where host=?", (host,)).fetchone()
        last = self.db.execute("select max(ts) from attempts where host=?", (host,)).fetchone()[0]
        earliest = max(row[0] if row else 0.0, (last or 0.0) + gap)
        slot = max(now, earliest)
        self.db.execute("insert or replace into hosts values(?,?)",
                        (host, slot + gap + random.uniform(0, jitter)))
        return slot

    # -- paper-level API -----------------------------------------------------------------
    def check(self, channel: str, host: Optional[str] = None) -> None:
        """Raise :class:`LimitReached` if one more attempt on (channel, host) is not allowed."""
        self._check(channel, host)

    def acquire(self, channel: str, host: Optional[str] = None, *, pace: bool = True) -> int:
        """Atomically check the caps, reserve one attempt and (if ``pace``) a per-host time slot,
        then sleep until that slot. Returns a reservation id for :meth:`record`.

        ``host=None`` reserves against the channel and global caps only (per-host pacing is
        then done per HTTP request by :class:`PacedAdapter`).
        """
        lim = self._lim(channel)
        with self._tx():
            self._check(channel, host)
            slot = time.time()
            if host and pace:
                slot = self._reserve_slot(host, lim["gap_s"], lim["jitter_s"])
            cur = self.db.execute(
                "insert into attempts values(?,?,?,?,?,?,?,?,?)",
                (slot, self.today(), channel, host or "", "", "pending", None, 0, ""),
            )
            rid = cur.lastrowid
        wait = slot - time.time()
        if wait > 0:
            time.sleep(wait)
        return rid

    def record(self, channel: str, host: Optional[str], doi: str, status: str,
               http: Optional[int] = None, nbytes: int = 0, note: str = "",
               reservation: Optional[int] = None) -> None:
        """Finalize a reservation from :meth:`acquire`, or log a new attempt if none is given."""
        with self._tx():
            if reservation is not None:
                self.db.execute(
                    "update attempts set doi=?, status=?, http=?, bytes=?, note=? where rowid=?",
                    (doi, status, http, nbytes, note[:300], reservation),
                )
            else:
                self.db.execute(
                    "insert into attempts values(?,?,?,?,?,?,?,?,?)",
                    (time.time(), self.today(), channel, host or "", doi, status, http, nbytes,
                     note[:300]),
                )

    def block(self, host: str, reason: str, seconds: float = COOLDOWN_S) -> None:
        with self._tx():
            self.db.execute("insert or replace into cooldown values(?,?,?)",
                            (host, time.time() + seconds, reason[:200]))

    # -- request-level API (used by PacedAdapter) ----------------------------------------
    def pace_request(self, host: str, channel: str) -> None:
        """Before one HTTP request: honour cooldown, per-host daily cap and per-host gap."""
        if host in METADATA_HOSTS:
            gap, jitter, capped = METADATA_GAP_S, METADATA_JITTER_S, False
        else:
            lim = self._lim(channel)
            gap, jitter, capped = lim["gap_s"], lim["jitter_s"], host not in VENDOR_API_HOSTS
        with self._tx():
            msg = self.cooling(host)
            if msg:
                raise HostLimited(msg)
            if capped:
                n = self.db.execute("select count(*) from requests where day=? and host=?",
                                    (self.today(), host)).fetchone()[0]
                if n >= self.per_host_daily_requests:
                    raise HostLimited(f"{host} daily request cap {self.per_host_daily_requests} reached")
            slot = self._reserve_slot(host, gap, jitter)
            self.db.execute("insert into requests values(?,?,?,?)", (slot, self.today(), host, channel))
        wait = slot - time.time()
        if wait > 0:
            time.sleep(wait)
            # Another process may have hit a bot wall on this host while we waited for our
            # slot; re-check so a queued request is never sent into a fresh cooldown.
            msg = self.cooling(host)
            if msg:
                raise HostLimited(msg)


class PacedAdapter(HTTPAdapter):
    """``requests`` transport adapter that routes every request through a :class:`Ledger`.

    Mount it on a session (``session.mount("https://", PacedAdapter(ledger, ...))``). Each
    request, including every redirect hop, is paced and capped by its real host, and a
    429 / CAPTCHA / Akamai "Access Denied" response puts that host into cooldown.
    ``channel`` may be a string or a zero-argument callable returning the current channel.
    """

    def __init__(self, ledger: Ledger, channel: "str | Callable[[], str]" = "oa", **kw):
        super().__init__(**kw)
        self.ledger = ledger
        self._channel = channel

    def send(self, request, **kwargs):  # type: ignore[override]
        host = (urlsplit(request.url).hostname or "").lower()
        channel = self._channel() if callable(self._channel) else self._channel
        self.ledger.pace_request(host, channel)
        response = super().send(request, **kwargs)
        ctype = (response.headers.get("Content-Type") or "").lower()
        if response.status_code in (403, 429, 503):
            body = self._peek(response)
            cooldown = block_signal(response.status_code, body)
            if cooldown:
                self.ledger.block(host, f"HTTP {response.status_code} block signal ({channel})",
                                  seconds=cooldown)
        elif 200 <= response.status_code < 300 and "html" in ctype:
            # Some bot walls answer 200 with a challenge page. Cool the host down and refuse
            # the response, so no client can save the challenge HTML as a "PDF".
            body = self._peek(response)
            if challenge_page(body):
                self.ledger.block(host, f"HTTP {response.status_code} challenge page ({channel})",
                                  seconds=COOLDOWN_S)
                response.close()
                raise HostLimited(f"{host} served a challenge page (HTTP {response.status_code}); "
                                  f"cooling down {COOLDOWN_S // 3600} h")
        return response

    @staticmethod
    def _peek(response) -> bytes:
        try:
            return response.content[:20000]  # cached on the response, later reads still work
        except Exception:  # noqa: BLE001
            return b""


def install(ledger: Ledger, sessions, channel: "str | Callable[[], str]") -> None:
    """Mount a :class:`PacedAdapter` on each ``requests.Session`` in ``sessions``."""
    for s in sessions:
        if s is None:
            continue
        adapter = PacedAdapter(ledger, channel)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
