---
name: paper-download
description: >
  Use when the user wants full-text PDFs for papers they already identified: one or more
  DOIs, doi.org links, or a reference list ("下载这几篇论文的 PDF", "把这个 DOI 列表下下来",
  "download the PDFs for these references"). Tries open access first, then the publisher's
  TDM API if the user has a key, then (opt-in) the user's own logged-in Chrome. Not for
  finding papers, Zotero import, or bypassing paywalls.
---

# paper-download

Command: `auto-paper-download` (from https://github.com/jxtse/auto-paper-harvester).

## 0. Install once

```bash
auto-paper-download --help >/dev/null 2>&1 || \
  uv tool install git+https://github.com/jxtse/auto-paper-harvester   # or: pipx install git+...
auto-paper-download doctor
```

`doctor` prints three lines, one per download step:

1. **Open access** (OpenAlex / Unpaywall / Crossref). Needs an e-mail address, sent to those
   APIs as a contact. If the line starts with `--`, ask the user which e-mail to use, then
   write it to `~/.auto-paper-download/.env` as `OPENALEX_MAILTO=`, `UNPAYWALL_EMAIL=` and
   `CROSSREF_MAILTO=` (one per line). Do not invent an address.
2. **Publisher TDM APIs** (Elsevier / Wiley / Springer). Optional. Without a key that step is
   skipped. Never ask the user to paste keys into the chat; suggest `auto-paper-download ui`,
   which stores them locally.
3. **Browser** (the user's own Chrome via the Playwright Extension). Optional and manual:
   the user installs the extension, logs in to their library, and pastes the token in
   `auto-paper-download ui`. Only use it if `doctor` shows it ready.

## 1. Download

```bash
auto-paper-download get 10.1016/j.jocs.2020.101171 10.5194/npg-20-705-2013 -o papers --json
auto-paper-download get --file refs.txt -o papers --json          # any text with DOIs in it
auto-paper-download get --file refs.txt -o papers --json --browser extension   # step 3 on
```

- Tell the user first: how many DOIs, where the files go, whether step 3 is on.
- Add `--browser extension` only if `doctor` line 3 is `OK` and the user agreed to use their
  Chrome. It opens one tab. If a CAPTCHA does not clear by itself the paper is skipped and
  the site cools down for 24 h. Never solve a CAPTCHA for the user.
- Daily caps and per-site pacing are on by default (ledger at
  `~/.auto-paper-download/ledger.sqlite`). Do not pass `--no-ledger` or raise limits
  unless the user asks. A `rate_limited` or `host_limited` reason means "try again tomorrow".
- Download only papers the user selected. If the list has more than ~50 DOIs, confirm first.

## 2. Report

`--json` prints `{"requested", "pdfs", "failed": [{"doi", "reason"}], "output_dir"}`.
Report: `<pdfs>/<requested>` saved to `<output_dir>`, then each failed DOI with its reason in
plain words:

| reason contains | tell the user |
|---|---|
| `no_source` / all sources `DownloadError` | no open copy found; a TDM key or step 3 may get it |
| `rate_limited` / `host_limited` / `cooldown` | daily limit or a site cooling down; retry later |
| `challenge` | the site showed a bot check; it was not solved |
| `auth_redirect` | their browser is not logged in to the library |

Files: `<output_dir>/<doi_slug>/<doi_slug>.pdf` plus any supplementary PDFs next to it.
Existing files are skipped on re-runs.

More: `auto-paper-download get -h`, and the repo README.
