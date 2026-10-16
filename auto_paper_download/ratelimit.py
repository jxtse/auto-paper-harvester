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
2. **Per-host spacing**: a minimum gap (+ random jitter) between two requests to the
   same host, whatever process sent the previous one.
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
from typing import Optional

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
PER_HOST_DAILY_ATTEMPTS = 25  # any single host, any channel
COOLDOWN_S = 24 * 3600        # after a bot-wall signal
COOLDOWN_429_S = 6 * 3600     # after HTTP 429

_BLOCK_PATTERNS = (b"captcha", b"cf-chl", b"just a moment", b"verify you are human",
                   b"attention required", b"are you a robot")


class LimitReached(RuntimeError):
    """Raised when a request is not allowed right now (cap reached or host cooling down)."""


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
    """SQLite-backed rate limiter. Safe to share between processes (one file, short transactions)."""

    path: Path
    limits: dict = field(default_factory=lambda: _env_limits(DEFAULT_LIMITS))
    global_daily_ok: int = int(os.environ.get("AUTO_PAPER_GLOBAL_DAILY_OK", GLOBAL_DAILY_OK))
    per_host_daily: int = int(os.environ.get("AUTO_PAPER_PER_HOST_DAILY", PER_HOST_DAILY_ATTEMPTS))

    def __post_init__(self) -> None:
        self.path = Path(self.path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.executescript(
            """
            create table if not exists attempts(ts real, day text, channel text, host text, doi text,
                                                status text, http int, bytes int, note text);
            create table if not exists cooldown(host text primary key, until real, reason text);
            create index if not exists a_day on attempts(day, channel);
            """
        )
        self.db.commit()

    @classmethod
    def from_env(cls) -> Optional["Ledger"]:
        path = os.environ.get("AUTO_PAPER_LEDGER")
        return cls(Path(path)) if path else None

    @staticmethod
    def today() -> str:
        return time.strftime("%Y-%m-%d")

    def _count(self, where: str, args: tuple) -> int:
        return self.db.execute(
            f"select count(*) from attempts where day=? and {where}", (self.today(), *args)
        ).fetchone()[0]

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

    def check(self, channel: str, host: str) -> None:
        """Raise :class:`LimitReached` if a request on (channel, host) is not allowed today."""
        lim = self._lim(channel)
        msg = self.cooling(host)
        if msg:
            raise LimitReached(msg)
        if self._count("status='ok'", ()) >= self.global_daily_ok:
            raise LimitReached(f"global daily cap {self.global_daily_ok} reached")
        if self._count("channel=? and status='ok'", (channel,)) >= lim["daily_ok"]:
            raise LimitReached(f"{channel} daily download cap {int(lim['daily_ok'])} reached")
        if self._count("channel=?", (channel,)) >= lim["daily_attempts"]:
            raise LimitReached(f"{channel} daily attempt cap {int(lim['daily_attempts'])} reached")
        if self._count("host=?", (host,)) >= self.per_host_daily:
            raise LimitReached(f"{host} daily attempt cap {self.per_host_daily} reached")

    def acquire(self, channel: str, host: str) -> None:
        """:meth:`check`, then sleep until the per-host gap (+ jitter) since the last request has passed."""
        self.check(channel, host)
        lim = self._lim(channel)
        last = self.db.execute("select max(ts) from attempts where host=?", (host,)).fetchone()[0] or 0
        wait = last + lim["gap_s"] + random.uniform(0, lim["jitter_s"]) - time.time()
        if wait > 0:
            time.sleep(wait)

    def record(self, channel: str, host: str, doi: str, status: str, http: Optional[int] = None,
               nbytes: int = 0, note: str = "") -> None:
        self.db.execute(
            "insert into attempts values(?,?,?,?,?,?,?,?,?)",
            (time.time(), self.today(), channel, host, doi, status, http, nbytes, note[:300]),
        )
        self.db.commit()

    def block(self, host: str, reason: str, seconds: float = COOLDOWN_S) -> None:
        self.db.execute("insert or replace into cooldown values(?,?,?)",
                        (host, time.time() + seconds, reason[:200]))
        self.db.commit()
