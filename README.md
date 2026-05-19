# Auto Paper Harvester

Batch-download paper PDFs (and supplementary files) by DOI. Routes each DOI through
publisher TDM APIs → open-access aggregators → optional institutional-browser fallback,
so you actually get the PDFs your institution is paying for instead of a wall of 403s.

```
Publisher TDM APIs (Wiley / Elsevier / Springer)
        │  (on failure)
        ▼
Crossref / OpenAlex / Unpaywall  (open-access fallback)
        │  (still failing, with --use-browser-fallback)
        ▼
Playwright + Chromium  (reuses your institutional cookies)
```

Each article ends up in `downloads/pdfs/<doi-slug>/<doi-slug>.pdf` with any
supplementary PDFs detected on the landing page saved alongside it. Throughput is
throttled to satisfy publisher TDM rate limits (≥ 1 s/file by default).

**v0.2.0 highlights**: 24 DOI-prefix routing table covering 19 publisher families
(see [docs/SUPPORTED_PUBLISHERS.md](docs/SUPPORTED_PUBLISHERS.md)); new
`--use-browser-fallback` Playwright pass for paywalled publishers without a public
TDM API (ACS, RSC, IEEE, AIP, IOP, APS, ...); failed-DOI tracking with structured
residual-failure summary; pre-packaged agent skill at
[`.claude/skills/paper-download/`](.claude/skills/paper-download/SKILL.md).

---

## 🚀 Quick start

```bash
git clone https://github.com/jxtse/auto-paper-harvester.git
cd auto-paper-harvester
pip install -e .                           # core (API + OA pipeline)
cp .env.example .env                       # then edit — set at least CROSSREF_MAILTO

# Single DOI
python .claude/skills/paper-download/scripts/download_by_doi.py \
  --doi 10.1038/s41586-020-2649-2

# Or batch from a Web of Science export
python -m auto_paper_download --savedrecs savedrecs.xls
```

For paywalled publishers without a TDM API (ACS, RSC, IEEE, AIP, IOP, APS):

```bash
pip install -e '.[browser]' && playwright install chromium    # ~150 MB, one-time
python -m auto_paper_download --savedrecs savedrecs.xls --use-browser-fallback
```

PDFs land under `downloads/pdfs/<doi-slug>/`. Re-running skips files already on disk.

> The repo distinguishes **two audiences** — the rest of this README has one section
> for each. Both share the same install and the same `.env`; the only thing that
> differs is **how you invoke the tool**.

---

## 👤 For Humans

You're a researcher or developer who wants to run this on your own DOI list.

### Install

```bash
git clone https://github.com/jxtse/auto-paper-harvester.git
cd auto-paper-harvester
pip install -e .                           # requires pip ≥ 21.3 for PEP 660 editable
# OR with browser fallback:
pip install -e '.[browser]' && playwright install chromium
```

Alternative: `uv sync` if you prefer `uv` over `pip`.

### Configure credentials

```bash
cp .env.example .env
$EDITOR .env
```

At minimum set ONE of `CROSSREF_MAILTO` / `OPENALEX_MAILTO` to a real email — public
APIs require this for polite-pool access. Other credentials are all optional:

| Variable | Used for | Free to get? |
|---|---|---|
| `CROSSREF_MAILTO` / `OPENALEX_MAILTO` | Polite-pool access (any email works) | ✅ |
| `UNPAYWALL_EMAIL` | OA fallback (any email works) | ✅ |
| `WILEY_TDM_TOKEN` | Wiley TDM API | ✅ (apply via Wiley) |
| `ELSEVIER_API_KEY` | Elsevier TDM API | ✅ (apply via Elsevier) |
| `SPRINGER_API_KEY` | Springer OA API | ✅ (apply via Springer) |
| `CROSSREF_REQUEST_DELAY` / `WILEY_REQUEST_DELAY` | Optional throttling overrides | — |

Missing credentials silently disable that path — they don't block the others.

### Three ways to invoke

#### 1. From a Web of Science export

Export `savedrecs.xls` from WoS, drop it in the project root, then:

```bash
python -m auto_paper_download --savedrecs savedrecs.xls --verbose
```

#### 2. From a DOI list

```bash
# Single DOI
python .claude/skills/paper-download/scripts/download_by_doi.py \
  --doi 10.1038/s41586-020-2649-2 --verbose

# Multiple DOIs from a file (one DOI per line)
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi-file ./dois.txt --resume --delay 1.5 --verbose
```

#### 3. With browser fallback (for ACS / RSC / IEEE / AIP / IOP / APS)

These publishers have no public TDM API; the only realistic way to get the PDF is
to reuse your institutional SSO session. Add `--use-browser-fallback`:

```bash
python -m auto_paper_download --savedrecs savedrecs.xls --use-browser-fallback
```

The first run opens a Chromium window so you can log into your university's SSO
once. Cookies persist under `~/.cache/auto_paper_download/browser_profile/` (or the
platform equivalent) for subsequent runs. See
[SKILL.md → Browser fallback setup](.claude/skills/paper-download/SKILL.md#browser-fallback-setup-one-time)
for cross-browser options (Chrome / Edge / Brave).

### Common flags

| Flag | Purpose |
|---|---|
| `--savedrecs <path>` | One or more WoS exports |
| `--output-dir <path>` | Where PDFs land (default `downloads/pdfs`) |
| `--delay <sec>` | Throttle between requests (≥ 1.0 enforced) |
| `--max-per-publisher <N>` | Cap per publisher (smoke tests) |
| `--overwrite` | Re-download even if file exists |
| `--dry-run` | Show routing plan, don't download |
| `--use-browser-fallback` | Enable Playwright second pass |
| `--verbose` | Debug logs |

`download_multiple_dois.py` adds: `--doi-file`, `--resume`, `--checkpoint-file`,
`--batch-size`, `--batch-index`.

### What you get

After every run the CLI prints a per-publisher summary:

```
Publisher PDF download summary:
  Crossref: 12/15 PDFs succeeded (80.0%)
  Elsevier: 28/30 PDFs succeeded (93.3%)
  Wiley: 8/8 PDFs succeeded (100.0%)
  BrowserFallback (browser fallback): 4/5 PDFs succeeded (80.0%)
3 DOI(s) could not be downloaded:
  - 10.1109/TPAMI.2024.999  (auth_redirect: Bounced to SSO at 'https://sso.uni.edu/...')
  ...
```

Residual failures with `auth_redirect`: log into the browser the fallback opened,
then rerun — already-downloaded PDFs are skipped, only the failed ones are retried.

---

## 🤖 For AI Agents

You're an AI agent (Claude Code / Codex CLI / Copilot / Cursor / Gemini / OpenClaw / ...)
and a user just pointed you at this repo asking you to "install it as a skill" or
"download these papers for me". This section is your runbook.

### Step 1 — Install the skill

The skill lives at `.claude/skills/paper-download/` (`SKILL.md` + scripts that wrap
the `auto_paper_download` package). **Two ways to wire it in:**

#### Option A — Repo-local (simplest, recommended for one-off use)

Clone the repo, install the package editable, and call the skill scripts from inside:

```bash
git clone https://github.com/jxtse/auto-paper-harvester.git
cd auto-paper-harvester
pip install -e '.[browser]' && playwright install chromium
cp .env.example .env       # edit: set CROSSREF_MAILTO at minimum
```

Now `python .claude/skills/paper-download/scripts/download_by_doi.py --doi <DOI>`
works directly. Your framework discovers `SKILL.md` either via repo-local skill
lookup or because the user added the repo to their workspace.

#### Option B — Globally installed skill

Copy the skill directory into your framework's skill discovery path:

```bash
# Same install as Option A, then:

# Claude Code (CLI / VS Code) and Claude Agent SDK:
cp -r .claude/skills/paper-download ~/.claude/skills/

# Codex CLI:
cp -r .claude/skills/paper-download ~/.codex/skills/

# Copilot CLI / VS Code agent (project-local):
mkdir -p .github/skills && cp -r .claude/skills/paper-download .github/skills/

# OpenClaw:
cp -r .claude/skills/paper-download ~/.openclaw/skills/

# Any framework (project-local fallback):
mkdir -p .agents/skills && cp -r .claude/skills/paper-download .agents/skills/
```

**`pip install -e .` is still required** (the skill scripts import
`auto_paper_download`, which is provided by this repo, not PyPI). Without it,
copied-out scripts fail with `ModuleNotFoundError: No module named 'auto_paper_download'`.

The `.env` file must live in whatever directory the agent runs the scripts from
(the scripts read `.env` relative to cwd, not the skill directory).

### Step 2 — Detect what the user wants and decide flags automatically

Apply these rules without asking the user every time:

| Condition | Action |
|---|---|
| DOI list contains `10.1021` / `10.1039` / `10.1126` / `10.1109` / `10.1063` / `10.1088` / `10.1103` / `10.1146` / `10.1080` | **Add `--use-browser-fallback`** — these have no public TDM API |
| DOI file has > 100 entries | **Add `--resume --batch-size 500`** — protects against network drops |
| User didn't explicitly say "redownload" | **Don't add `--overwrite`** — cached files should stay |
| First time with `--use-browser-fallback` on this machine | **Warn before launch**: "A browser window will open — please complete your university SSO login once; cookies will be cached for next time." |
| User's `.env` is missing or has no credentials | **Surface the warning verbatim** — don't proceed silently. Ask which credentials they have and offer to edit `.env` |

### Step 3 — Invoke

Pick the entry point that matches the input shape:

```bash
# Single DOI
python <skill_dir>/scripts/download_by_doi.py \
  --doi <DOI> [--use-browser-fallback]

# Multiple DOIs (file or flag-repeat)
python <skill_dir>/scripts/download_multiple_dois.py \
  --doi-file dois.txt [--resume] [--batch-size N] [--use-browser-fallback]

# WoS bulk export
python -m auto_paper_download \
  --savedrecs savedrecs.xls [--use-browser-fallback]
```

After the run, parse the summary stdout: report `succeeded/attempted` per publisher
and list any residual failures (especially `auth_redirect` ones — those are actionable
by the user).

### Pre-flight checklist

Before downloading, confirm:

1. **DOIs are valid** — each line matches `10.\d{4,9}/.+`. Malformed entries are silently dropped.
2. **Institution likely subscribes** to the publishers in the list — browser fallback
   only works for content they actually have access to. For pure-OA lists, browser
   fallback adds nothing over Unpaywall.
3. **Output dir is acceptable** — default `./downloads/pdfs/`. Confirm if running on the user's machine.

### Decision-tree references

- Full runbook (trigger phrases / output layout / troubleshooting):
  [`.claude/skills/paper-download/SKILL.md`](.claude/skills/paper-download/SKILL.md)
- Per-publisher routing + which need browser fallback:
  [`docs/SUPPORTED_PUBLISHERS.md`](docs/SUPPORTED_PUBLISHERS.md)
- Adding a new publisher (CSS selectors etc.): same SUPPORTED_PUBLISHERS.md, end of file.

---

## 📖 Reference

### Performance

- **Throughput**: ~40 PDFs/min at default `--delay 1.5s`, up to ~60 PDFs/min at the
  enforced minimum of `--delay 1.0s`. Real numbers depend on network/API latency.
- **Success rate**: with `UNPAYWALL_EMAIL` + Crossref/OpenAlex credentials, mixed
  DOI sets typically hit ~90% overall; individual publishers reach 88-95% when their
  API keys are configured. Browser fallback pushes paywalled-publisher hit rate
  another 10-20 percentage points for institutional users.

**Why it performs well**:
- Precise DOI-prefix routing (`auto_paper_download/publishers.py`) — minimal futile attempts.
- Throttle ≥ 1 s/file — avoids 429/403 bans.
- Layered OA fallback — Wiley API miss → Crossref → OpenAlex → Unpaywall.
- Optional browser pass — closes the long tail of TDM-less publishers.
- SI capture in the same pass.

### Supplementary materials

After downloading a PDF, the tool fetches the landing page, looks for supplement-style
links (`supplementary`, `SI`, `supporting information`, etc.), and downloads only ones
that resolve to PDFs. Non-PDF assets (datasets, archives, videos) are skipped to avoid
pulling gigabytes by accident. Files are sanitised and saved next to the article PDF.

This is best-effort — paywalls, JS-driven pages, or unconventional link structures may
prevent automatic collection. Failures are logged as warnings, not errors.

### Publisher coverage

The router recognises **24 DOI prefixes** across 19 publisher families, in 5 support
tiers (`full` / `oa_only` / `partial` / `browser_only` / `unsupported`). See the full
table in [docs/SUPPORTED_PUBLISHERS.md](docs/SUPPORTED_PUBLISHERS.md).

Quick overview:

| Tier | Publishers |
|---|---|
| `full` (TDM API) | Wiley, Elsevier |
| `oa_only` | Springer Nature, Nature Portfolio, BMC |
| `partial` (mostly OA) | PNAS, Beilstein |
| `browser_only` (need `--use-browser-fallback`) | ACS, RSC, AAAS/Science, ECS, IOP, AIP, AVS, IEEE, APS, Annual Reviews, Taylor & Francis, Optica/OSA, KPS |

### Tips & troubleshooting

- **HTTP 403 / 429**: rate limit; raise `--delay` or set per-publisher delays in `.env`.
- **`ModuleNotFoundError: auto_paper_download`**: you copied the skill out without running `pip install -e .` first.
- **`editable mode currently requires a setuptools-based build`**: upgrade pip with `python -m pip install --upgrade pip` (need pip ≥ 21.3 for PEP 660).
- **`playwright: command not found`**: `pip install playwright && playwright install chromium`.
- **Browser fallback always returns `no_link`**: publisher updated their layout — add a CSS selector to `PUBLISHER_PDF_SELECTORS` in `auto_paper_download/browser_fallback.py` (procedure in SUPPORTED_PUBLISHERS.md).
- **Springer 403 on a DOI you think you have access to**: Springer's public API only serves OA content; use `--use-browser-fallback` with your institutional session.
- **Non-OA content from ACS/RSC even with browser fallback**: your institution doesn't have the subscription. This is not a paywall bypass — it relies on your real entitlements.
- **Extending to a new publisher**: see the "Adding a new publisher" section in [docs/SUPPORTED_PUBLISHERS.md](docs/SUPPORTED_PUBLISHERS.md).

---

## License

See `LICENSE` (if present) or contact the repo owner.
