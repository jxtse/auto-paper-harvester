# Rate limits, daily caps and cooldowns

Publishers ban by IP. The most common way to get a campus IP blocked is not one big run; it
is many small runs in a day, plus retrying a publisher that has already started answering
with CAPTCHAs. `--delay` only spaces requests *within* one run. The ledger adds limits that
hold across runs and processes.

```bash
python -m auto_paper_download --savedrecs savedrecs.xls --ledger ~/.cache/auto_paper_download/ledger.sqlite
# or
export AUTO_PAPER_LEDGER=~/.cache/auto_paper_download/ledger.sqlite
```

Without a ledger the tool behaves as before.

## What it enforces

1. **Daily caps per channel**, on successful downloads and on attempts (a failed request
   still counts as an attempt). Days are local calendar days.
2. **A global daily cap** on successful downloads across all channels (default 80).
3. **A per-host attempt cap** (default 25 per day), whatever the channel.
4. **A minimum gap between two requests to the same host**, plus random jitter, measured
   against the last request in the ledger, so it also holds between separate runs.
5. **Cooldowns.** A block signal puts the host on hold:
   - HTTP 429 → 6 h
   - CAPTCHA / challenge page ("Just a moment...", "verify you are human", ...) or an
     Akamai-style "403 Access Denied" → 24 h

   A plain 403 is usually "no subscription", not a ban, and does not trigger a cooldown.

DOIs refused by the ledger are reported as `rate_limited: ...` and are **not** passed on to
the browser fallback. Retrying a refused request in a browser would defeat the limit.

## Defaults

| Channel | Used for | ok / day | attempts / day | gap per host | jitter |
|---|---|---:|---:|---:|---:|
| `oa` | Crossref / Unpaywall / OpenAlex OA links | 40 | 80 | 4 s | +0–3 s |
| `elsevier` | Elsevier TDM API | 30 | 45 | 3 s | +0–2 s |
| `wiley` | Wiley TDM API | 30 | 45 | 8 s | +0–4 s |
| `springer` | Springer API | 30 | 45 | 4 s | +0–3 s |
| `inst` | Plain HTTP on an institutional IP (custom callers) | 15 | 25 | 45 s | +0–45 s |
| `browser` | Browser fallback (either mode) | 12 | 20 | 60 s | +0–60 s |

Browser requests are paced per publisher (keyed by DOI prefix, e.g. `browser:10.1021`).

These numbers are conservative on purpose. The intended workflow is: search and screen
abstracts first, then download only the papers you will actually read or use. If you need
more, raise a limit deliberately rather than by default.

## Overrides

| Variable | Format | Example |
|---|---|---|
| `AUTO_PAPER_LIMIT_<CHANNEL>` | `ok_per_day,attempts_per_day,gap_s,jitter_s` | `AUTO_PAPER_LIMIT_BROWSER=6,10,90,60` |
| `AUTO_PAPER_GLOBAL_DAILY_OK` | int | `50` |
| `AUTO_PAPER_PER_HOST_DAILY` | int | `15` |

## Inspecting and resetting

The ledger is plain SQLite (`attempts`, `cooldown` tables):

```bash
sqlite3 ~/.cache/auto_paper_download/ledger.sqlite \
  "select channel, status, count(*) from attempts where day=date('now','localtime') group by 1,2"
sqlite3 ~/.cache/auto_paper_download/ledger.sqlite "select host, datetime(until,'unixepoch','localtime'), reason from cooldown"
```

From Python: `Ledger(path).usage()` returns today's counts and active cooldowns.
To lift a cooldown early (only once you know why it happened):
`sqlite3 ledger.sqlite "delete from cooldown where host='www.example.com'"`.
