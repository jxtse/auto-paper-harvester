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

# Paper Download — Agent Runbook

> Slim runbook for agent use. Configuration depth and human-facing reference
> material live in the project docs:
> - **[../../../README.md](../../../README.md)** — install + invoke (humans and agents)
> - **[../../../docs/BROWSER_FALLBACK.md](../../../docs/BROWSER_FALLBACK.md)** — every browser-fallback knob
> - **[../../../docs/SUPPORTED_PUBLISHERS.md](../../../docs/SUPPORTED_PUBLISHERS.md)** — per-publisher routing + tier table

## When to invoke

**Invoke for:**
- "下载 DOI 10.x/y 的 PDF" / "把这几个 DOI 的全文下下来" / "批量下这个 DOI 列表"
- "Download paper(s) for DOI(s) ..." / "Bulk-download from this WoS export"

**Don't invoke for:**
- Paper *search* (use Semantic Scholar / arXiv / web search)
- Downloading *all references* of one paper (use `ref-downloader` skill — different scope)
- Zotero import / metadata sync

## Prerequisites (verify once per workspace)

1. `auto_paper_download` is importable. If not: `pip install -e .` from the repo root
   (add `[browser]` extra + `playwright install chromium` for browser fallback).
2. A `.env` exists in the cwd the scripts will run from. Minimum: `CROSSREF_MAILTO`
   (any real email). Missing creds don't error — they silently disable that path,
   so surface the warning to the user verbatim if creds are empty.

## Pre-flight (per invocation)

1. **Echo back what you'll do**: `即将下载 N 个 DOI：<前 3 个示例>`
2. **DOIs valid**? Each line matches `10.\d{4,9}/.+`. Malformed lines are silently dropped.
3. **Output dir agreed**? Default `./downloads/pdfs/`. Confirm if running on user's machine.

## Auto-flag rules (apply without asking)

| Condition | Action |
|---|---|
| DOI list contains any of `10.1021` `10.1039` `10.1126` `10.1109` `10.1063` `10.1088` `10.1103` `10.1146` `10.1080` | Add `--use-browser-fallback` (these have no public TDM API) |
| DOI file has > 100 entries | Add `--resume --batch-size 500` |
| User didn't say "redownload" / "refresh" | Don't add `--overwrite` |
| First `--use-browser-fallback` on this machine | Warn: "A Chromium window will open — please complete your university SSO login once; cookies are cached for next time" |

## Commands

```bash
# Single DOI
python .claude/skills/paper-download/scripts/download_by_doi.py \
  --doi <DOI> [--use-browser-fallback] [--verbose]

# Multiple DOIs (flag-repeat OR --doi-file)
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi-file ./dois.txt [--resume] [--batch-size 500] [--use-browser-fallback]

# WoS savedrecs.xls bulk
python -m auto_paper_download \
  --savedrecs ./savedrecs.xls [--use-browser-fallback] [--verbose]
```

## Output layout

```
<output_dir>/                       # default: ./downloads/pdfs/
├── <doi_slug>/
│   ├── <doi_slug>.pdf              # main PDF
│   └── <doi_slug>_SI_1.pdf         # supplementary PDFs when found
├── _browser_fallback/              # PDFs recovered by the browser pass
│   └── <doi_slug>.pdf
└── state/                          # multi-DOI script only
    ├── <name>.checkpoint.json
    ├── <name>_successes.txt
    └── <name>_failures.txt
```

`<doi_slug>` = lowercased DOI with `[^A-Za-z0-9._-]` replaced by `_`.

## Reading the summary

The CLI prints per-publisher tallies and then up to 20 residual failures:

```
Publisher PDF download summary:
  Crossref: 12/15 PDFs succeeded (80.0%)
  Elsevier: 28/30 PDFs succeeded (93.3%)
  BrowserFallback: 4/5 PDFs succeeded (80.0%)
3 DOI(s) could not be downloaded:
  - 10.1109/TPAMI.2024.999  (auth_redirect: Bounced to SSO at 'https://sso.uni.edu/...')
```

**Report back to the user:**
- Overall succeeded/attempted counts
- Per-publisher numbers if mixed publishers
- Residual failures **with their reasons** — especially `auth_redirect` ones (user
  needs to log in once via the browser window, then rerun)

## Common flags

| Flag | Purpose |
|---|---|
| `--use-browser-fallback` | Enable Playwright second pass (see auto-flag rules above) |
| `--resume` | Skip DOIs in the checkpoint (multi-DOI script) |
| `--batch-size N` / `--batch-index I` | Process slice `[I*N, (I+1)*N)` |
| `--delay <sec>` | Throttle between requests (≥ 1.0s enforced) |
| `--overwrite` | Re-download even if file exists |
| `--dry-run` | Show routing without downloading |
| `--verbose` | Per-DOI download plan + selector debug |
| `--output-dir <path>` | Override default `./downloads/pdfs/` |

## When something goes wrong

| Symptom | Likely cause / fix |
|---|---|
| `ModuleNotFoundError: auto_paper_download` | `pip install -e .` not run from repo root |
| `editable mode currently requires a setuptools-based build` | pip < 21.3; `python -m pip install --upgrade pip` |
| Publisher reports `0/N succeeded` | Missing API credential in `.env` (surface to user) |
| `auth_redirect` in browser fallback | User needs interactive SSO login in the Chromium window |
| `no_link` in browser fallback | Publisher updated their layout; selector list in [docs/SUPPORTED_PUBLISHERS.md](../../../docs/SUPPORTED_PUBLISHERS.md#adding-a-new-publisher) |
| Springer 403 on a DOI user has access to | Springer API serves OA only; rerun with `--use-browser-fallback` |
| All ACS/RSC DOIs fail and no `--use-browser-fallback` was used | Re-invoke with the flag (no TDM API for these publishers) |

For deeper troubleshooting of the browser fallback (channels, profile location,
headless vs headed, all env vars), see
[docs/BROWSER_FALLBACK.md](../../../docs/BROWSER_FALLBACK.md).
