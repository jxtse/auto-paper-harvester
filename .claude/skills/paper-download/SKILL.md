---
name: paper-download
description: Download paper PDFs by single or multiple DOIs using auto_paper_download with ready-to-run scripts and clear usage.
---

# Paper Download Skill

## Overview
- This Skill helps you quickly download a paper PDF by a single DOI, or batch download PDFs for multiple DOIs.
- It leverages the `auto_paper_download` package and provides two ready-to-run scripts.
- PDFs are saved under `downloads/pdfs/<doi-slug>/` with supplementary PDFs (if found) saved next to the main PDF.

## Prerequisites
- Python environment set up for this project (e.g., `uv sync`).
- A `.env` file in the project root (copy from `.env.example`) with any credentials you have:
  - `WILEY_TDM_TOKEN` (Wiley TDM API)
  - `ELSEVIER_API_KEY` (Elsevier TDM API)
  - `SPRINGER_API_KEY` (optional, open-access only)
  - `CROSSREF_MAILTO`, `OPENALEX_MAILTO` (contact email for polite API usage)
  - `UNPAYWALL_EMAIL` (optional, enables OA fallback)
  - `CROSSREF_REQUEST_DELAY`, `WILEY_REQUEST_DELAY` (optional throttling)
- Missing credentials simply disable that provider. Provide at least one `CROSSREF_MAILTO` or `OPENALEX_MAILTO`.

## Scripts
- `scripts/download_by_doi.py`: download a single DOI.
- `scripts/download_multiple_dois.py`: download multiple DOIs (via repeated flags or a file).

## DOI Examples and Templates
- **`example_dois.txt`**: Ready-to-use example DOI file for testing.

See `DOI_EXAMPLES.md` for:
- Valid DOI formats (standard and URL forms)
- Publisher-specific DOI examples
- File naming conventions for batch downloads
- Complete usage examples and best practices

## Single DOI Usage
Run from the project root:

```bash
python .claude/skills/paper-download/scripts/download_by_doi.py --doi 10.1038/s41586-020-2649-2 --verbose
```

Options:
- `--output-dir` destination root, defaults to `downloads/pdfs`
- `--delay` throttle seconds, default `1.5` (minimum `1.0`)
- `--overwrite` re-download even if exists
- `--dry-run` inspect routing without downloading
- `--verbose` debug logs

## Multiple DOIs Usage
Provide DOIs directly or via a text file (one per line):

```bash
# Multiple DOIs via repeated flags
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi 10.1038/s41586-020-2649-2 \
  --doi 10.1002/anie.202100001 \
  --verbose

# From a file of DOIs
python .claude/skills/paper-download/scripts/download_multiple_dois.py --doi-file ./dois.txt --delay 1.5
```

Options:
- `--doi` repeatable flag to add DOIs
- `--doi-file` path to a file with one DOI per line
- `--output-dir`, `--delay`, `--max-per-publisher`, `--overwrite`, `--dry-run`, `--verbose`

### Resume and Batching
For large runs, you can resume from a checkpoint and/or run in batches:

```bash
# Resume from the last checkpoint (derived from --doi-file name)
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi-file ./dois.txt \
  --resume \
  --delay 1.5 --verbose

# Resume with a custom checkpoint file
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi-file ./dois.txt \
  --resume --checkpoint-file downloads/state/dois.checkpoint.json \
  --delay 1.5

# Batch execution: process 500 DOIs per run
# Run batch index 0, then 1, etc.
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi-file ./dois.txt --batch-size 500 --batch-index 0 --delay 1.5
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi-file ./dois.txt --batch-size 500 --batch-index 1 --delay 1.5
```

Reports and checkpoints:
- Checkpoints are stored under `downloads/state/` by default (derived from `--doi-file` name).
- Successes report: `downloads/state/<name>_successes.txt` (tab-separated DOI and saved path).
- Failures report: `downloads/state/<name>_failures.txt` (tab-separated DOI and error or NO_OUTPUT).
- Dry-run does not write checkpoints or reports.

## Behavior Notes
- The scripts automatically read `.env`. Missing providers are skipped gracefully.
- When publisher/Crossref/OpenAlex cannot serve a PDF, Unpaywall OA fallback is attempted if `UNPAYWALL_EMAIL` is set.
- Springer only returns open-access items; paywalled content still requires manual access.
- After downloading a PDF, a DOI landing page scan looks for supplementary links and saves PDF-only assets.
- Throttling ensures compliance with typical TDM limits (min `1.0s/file`).

## Troubleshooting
- 403/429 responses usually indicate rate limits or missing safelisting; use request delays and ensure credentials.
- Check logs for the exact URL that failed when extending to new publishers.