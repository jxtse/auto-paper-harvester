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

1. **Daily caps per channel**, on successful downloads and on attempts (a failed paper
   still counts as an attempt). Days are local calendar days.
2. **A global daily cap** on successful downloads across all channels (default 80).
3. **A per-host request cap** (default 100 HTTP requests per day; one paper with
   supplementary files can take ~10) on every publisher host,
   counted on the real host of each request, redirects included. Shared metadata APIs
   (OpenAlex, Crossref, Unpaywall, doi.org) and publisher TDM APIs (Elsevier, Wiley,
   Springer) are exempt; the per-channel paper caps bound them instead.
4. **A minimum gap between two requests to the same host**, plus random jitter. The gap
   comes from the channel table below; metadata APIs use 1 s + 0–1 s.

All of this is enforced **atomically across processes**: each paper reserves its slot in a
SQLite `BEGIN IMMEDIATE` transaction before any request is sent, and every HTTP request
books its per-host time slot the same way. Two runs sharing one ledger cannot both take
the last slot of a cap or send to one host at the same moment. A reservation whose run
crashed stops counting against the success caps after one hour.
5. **Cooldowns.** A block signal puts the host on hold:
   - HTTP 429 → 6 h
   - CAPTCHA / challenge page ("Just a moment...", "verify you are human", ...) or an
     Akamai-style "403 Access Denied" → 24 h

   A plain 403 is usually "no subscription", not a ban, and does not trigger a cooldown.

Failure reasons tell you which limit fired:

- `rate_limited: ...` — a channel or global cap. The DOI is **not** passed on to the browser
  fallback; retrying it in a browser would defeat the limit.
- `host_limited: ...` — one HTTP host is cooling down or at its request cap. The browser
  fallback may still retry it, under its own `browser` caps and its own per-publisher pacing.

In the browser fallback, only a real block signal (challenge page, Akamai "Access Denied",
429) cools a publisher down. A plain 403 there is usually a paywall and does not.

## Defaults

| Channel | Used for | ok / day | attempts / day | gap per host | jitter |
|---|---|---:|---:|---:|---:|
| `oa` | Crossref / Unpaywall / OpenAlex OA links | 40 | 80 | 4 s | +0–3 s |
| `elsevier` | Elsevier TDM API | 30 | 45 | 3 s | +0–2 s |
| `wiley` | Wiley TDM API | 30 | 45 | 8 s | +0–4 s |
| `springer` | Springer API | 30 | 45 | 4 s | +0–3 s |
| `inst` | Plain HTTP on an institutional IP (custom callers) | 15 | 25 | 45 s | +0–45 s |
| `browser` | Browser fallback (either mode) | 12 | 20 | 60 s | +0–60 s |

HTTP requests are paced per real host (`api.openalex.org`, `www.mdpi.com`, ...). Browser
requests are paced per publisher, keyed by DOI prefix (`browser:10.1021`), because the
browser follows redirects itself.

These numbers are conservative on purpose. The intended workflow is: search and screen
abstracts first, then download only the papers you will actually read or use. If you need
more, raise a limit deliberately rather than by default.

## Overrides

| Variable | Format | Example |
|---|---|---|
| `AUTO_PAPER_LIMIT_<CHANNEL>` | `ok_per_day,attempts_per_day,gap_s,jitter_s` | `AUTO_PAPER_LIMIT_BROWSER=6,10,90,60` |
| `AUTO_PAPER_GLOBAL_DAILY_OK` | int | `50` |
| `AUTO_PAPER_PER_HOST_DAILY_REQUESTS` | int, HTTP requests per publisher host per day | `60` |
| `AUTO_PAPER_PER_HOST_DAILY` | int, browser attempts per publisher per day | `15` |

## Inspecting and resetting

The ledger is plain SQLite: `attempts` (one row per paper), `requests` (one row per HTTP
request), `cooldown`, and `hosts` (next free time slot per host).

```bash
sqlite3 ~/.cache/auto_paper_download/ledger.sqlite \
  "select channel, status, count(*) from attempts where day=date('now','localtime') group by 1,2"
sqlite3 ~/.cache/auto_paper_download/ledger.sqlite "select host, datetime(until,'unixepoch','localtime'), reason from cooldown"
```

From Python: `Ledger(path).usage()` returns today's counts and active cooldowns.
To lift a cooldown early (only once you know why it happened):
`sqlite3 ledger.sqlite "delete from cooldown where host='www.example.com'"`.
