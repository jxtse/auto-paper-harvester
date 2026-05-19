---
name: paper-download
description: >
  Use when the user asks to download paper PDFs by one or more DOIs.
  Routes each DOI through publisher TDM APIs (Wiley/Elsevier/Springer),
  then OA fallbacks (OpenAlex/Crossref/Unpaywall), and — when --use-browser-fallback
  is set — a final Playwright pass that reuses institutional cookies for
  ACS/RSC/IEEE/AIP/IOP/APS. Not for paper search, Zotero import, or downloading
  the *references of* a paper (use ref-downloader for that).
---

# Paper Download Skill

## Overview

This skill downloads paper PDFs for a list of DOIs through a layered pipeline:

```
Publisher TDM API (Wiley / Elsevier / Springer)
        │  (on failure)
        ▼
Crossref / OpenAlex / Unpaywall  (open-access fallback)
        │  (still failing, with --use-browser-fallback)
        ▼
Playwright + Chromium  (reuse user's institutional cookies)
```

PDFs are saved under `downloads/pdfs/<doi-slug>/<doi-slug>.pdf` with any
supplementary PDFs found on the publisher landing page next to it.

The Playwright pass (added in this version) lifts ACS/RSC/IEEE/AIP/IOP/APS — publishers
that have no public TDM API — from "always-fails" to "usually-works", provided the user
has institutional access.

## When to invoke

**Invoke for:**
- "下载 DOI 10.x/y 的 PDF" / "把这几个 DOI 的全文下下来"
- "Download paper(s) for DOIs ..."
- "批量下这个 DOI 列表 / dois.txt"
- WoS savedrecs.xls export → bulk download

**Don't invoke for:**
- Paper *search* (use Semantic Scholar / arXiv / web search instead)
- Downloading *all references* of one paper (use ref-downloader instead — different scope)
- Zotero import / metadata sync (Zotero MCP)

## Pre-flight checklist

1. **DOI(s) collected?** Echo back: `即将下载 N 个 DOI：<样例>`
2. **`.env` configured?** At minimum `CROSSREF_MAILTO` or `OPENALEX_MAILTO`.
   Optional: `WILEY_TDM_TOKEN`, `ELSEVIER_API_KEY`, `SPRINGER_API_KEY`,
   `UNPAYWALL_EMAIL`. Missing creds → that publisher path is silently skipped.
3. **Output dir agreed?** Defaults to `downloads/pdfs/` relative to cwd.
4. **Browser fallback wanted?** If the DOI list contains ACS / RSC / IEEE / AIP / IOP /
   APS prefixes (`10.1021`, `10.1039`, `10.1109`, `10.1063`, `10.1088`, `10.1103`),
   strongly recommend `--use-browser-fallback`. First time using it: confirm Playwright
   is installed (see *Browser fallback setup* below).

## Primary entries

### Single DOI

```bash
python .claude/skills/paper-download/scripts/download_by_doi.py \
  --doi 10.1038/s41586-020-2649-2 --verbose
```

### Multiple DOIs (flag-repeat)

```bash
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi 10.1038/s41586-020-2649-2 \
  --doi 10.1002/anie.202100001 \
  --verbose
```

### Multiple DOIs (file input + checkpoint)

```bash
# dois.txt: one DOI per line
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi-file ./dois.txt \
  --resume \
  --delay 1.5 \
  --verbose
```

### With browser fallback (ACS/RSC/IEEE/AIP/IOP/APS heavy)

```bash
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi-file ./dois.txt \
  --use-browser-fallback \
  --resume \
  --verbose
```

### WoS export → bulk download

```bash
uv run python -m auto_paper_download --savedrecs ./savedrecs.xls \
  --use-browser-fallback --verbose
```

## Output layout

```
<output_dir>/
├── <doi_slug>/
│   ├── <doi_slug>.pdf            # main PDF
│   ├── <doi_slug>_SI_1.pdf       # supplementary PDFs when found
│   └── ...
├── _browser_fallback/            # PDFs recovered by the browser pass
│   └── <doi_slug>.pdf
└── state/                        # (multi-DOI script only) checkpoints + reports
    ├── <name>.checkpoint.json
    ├── <name>_successes.txt
    └── <name>_failures.txt
```

`<doi_slug>` = lowercased DOI with `[^A-Za-z0-9._-]` replaced by `_`.

## Configuration (.env)

```ini
# At minimum, set ONE polite-email field so the public APIs accept your requests
CROSSREF_MAILTO=you@example.com
OPENALEX_MAILTO=you@example.com

# Recommended: enables Unpaywall OA fallback (greatly improves overall hit rate)
UNPAYWALL_EMAIL=you@example.com

# Per-publisher TDM (each is optional; missing → that publisher path is skipped)
WILEY_TDM_TOKEN=
ELSEVIER_API_KEY=
SPRINGER_API_KEY=          # open-access content only

# Throttling
CROSSREF_REQUEST_DELAY=4.0
WILEY_REQUEST_DELAY=2.5
```

## Browser fallback setup (one-time)

The browser fallback is *opt-in* (`--use-browser-fallback`). It requires:

```bash
pip install playwright
playwright install chromium      # ~150 MB, one-time
```

First run will open a real Chromium window so the user can log into their
institution's SSO once. Cookies persist under
`~/.cache/auto_paper_download/browser_profile/` and carry over to subsequent runs.

Useful env vars:

| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_FALLBACK_ENABLED` | `1` | Set to `0` to disable globally (CI) |
| `BROWSER_FALLBACK_HEADLESS` | auto | `1` forces headless, `0` forces headed |
| `BROWSER_FALLBACK_CHANNEL` | `chromium` | Set `msedge` to use Edge instead |
| `BROWSER_FALLBACK_PROFILE` | `~/.cache/.../browser_profile` | Override profile dir |
| `BROWSER_FALLBACK_AUTH_HOSTS` | (none) | Comma-list of extra SSO host substrings |

### Browser fallback behavior

- Runs as a **second pass**: every DOI the API pipeline failed gets one retry.
- Uses publisher-specific CSS selectors (see `auto_paper_download/browser_fallback.py`
  → `PUBLISHER_PDF_SELECTORS`) plus a generic heuristic.
- Bounces to SSO → marked `auth_redirect`, surfaces as a residual failure with the
  reason; user logs in once and reruns.
- Headed mode is the default for ACS/Wiley (their SI capture is unreliable in headless).
- PDF integrity check (`%PDF-` magic bytes) — non-PDF payloads (CAPTCHA HTML) are
  rejected with `challenge_timeout`.

## Publisher coverage

The router (`auto_paper_download/publishers.py`) recognises **24 DOI prefixes** across:

| Tier | Publishers |
|---|---|
| `full` (TDM API) | Wiley, Elsevier |
| `oa_only` (Springer OA API or OpenAlex/Unpaywall) | Springer Nature, Nature Portfolio, BMC |
| `partial` (mostly OA) | PNAS, Beilstein |
| `browser_only` (need `--use-browser-fallback`) | ACS, RSC, AAAS/Science, ECS, IOP, AIP, AVS, IEEE, APS, Annual Reviews, Taylor & Francis, Optica/OSA, KPS |

Full table in [docs/SUPPORTED_PUBLISHERS.md](../../../docs/SUPPORTED_PUBLISHERS.md).

## Common flags (both scripts)

| Flag | Purpose |
|---|---|
| `--output-dir <path>` | Where PDFs land (default `downloads/pdfs`) |
| `--delay <sec>` | Throttle between requests (≥ 1.0s enforced) |
| `--overwrite` | Re-download even if file exists |
| `--dry-run` | Show routing without downloading |
| `--use-browser-fallback` | Enable Playwright second pass |
| `--verbose` | Debug logs |

`download_multiple_dois.py` extras: `--doi-file`, `--resume`, `--checkpoint-file`,
`--batch-size`, `--batch-index`, `--max-per-publisher`.

## Reading the summary

After every run the CLI prints:

```
Publisher PDF download summary:
  Crossref: 12/15 PDFs succeeded (80.0%)
  Elsevier: 28/30 PDFs succeeded (93.3%)
  Wiley: 8/8 PDFs succeeded (100.0%)
  BrowserFallback (browser fallback): 4/5 PDFs succeeded (80.0%)
3 DOI(s) could not be downloaded:
  - 10.1109/TPAMI.2024.999  (auth_redirect: Bounced to SSO/login at 'https://sso.uni.edu/...')
  ...
```

DOIs in the "could not be downloaded" list are the residual failures.
For `auth_redirect` reasons: log in once via the Chromium window the browser
fallback opens, then rerun with the same flags — the resume + cache will skip
everything already downloaded.

## Troubleshooting

- **HTTP 403/429**: rate limit; raise `--delay` or set per-publisher delays in `.env`.
- **`playwright: command not found`**: install with `pip install playwright && playwright install chromium`.
- **Browser fallback always returns `no_link`**: publisher updated their layout; add a
  selector to `PUBLISHER_PDF_SELECTORS` in `auto_paper_download/browser_fallback.py`.
- **Springer 403 on subscription DOI**: Springer API only serves OA content; user needs
  institutional access (browser fallback works if logged in).
- **Logs**: pass `--verbose` for per-DOI download plan + per-selector debug.

## See also

- `docs/SUPPORTED_PUBLISHERS.md` — per-publisher tier table and known issues
- `DOI_EXAMPLES.md` (project root) — DOI format reference and batch usage examples
- `auto_paper_download/browser_fallback.py` — selector definitions, easy to extend
