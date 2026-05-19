# Auto Paper Harvester

This is a command line helper that parses Web of Science `savedrecs.xls`
exports, identifies DOIs, selects the appropriate publisher interface, and downloads the
article PDF together with any Supplementary Information (SI) assets that can be detected
on the landing page. Each article ends up in
`downloads/pdfs/<doi-slug>/` with the main PDF named after the DOI slug,
plus any SI files located during scraping.

**v0.2.0 highlights**
- Publisher router rewritten — now recognises **24 DOI prefixes** across 19 publisher
  families (was 4). See [docs/SUPPORTED_PUBLISHERS.md](docs/SUPPORTED_PUBLISHERS.md).
- New **`--use-browser-fallback`** flag: after the HTTP/OA pipeline finishes, retry
  every failed DOI through a Playwright-driven Chromium session that reuses the user's
  institutional cookies. Lifts ACS / RSC / IEEE / AIP / IOP / APS from "always-fails"
  to "usually-works" for institutional users.
- Failed-DOI tracking + per-publisher residual-failure summary in the CLI output.
- Skill (`.claude/skills/paper-download/SKILL.md`) rewritten in agent-runbook style with
  trigger phrases, pre-flight checklist, output layout, and per-publisher tier table.

## Supported sources

| Layer | Sources |
|---|---|
| Publisher TDM APIs | Wiley, Elsevier |
| OA APIs | Springer Nature OA, OpenAlex, Crossref, Unpaywall |
| Browser fallback (opt-in) | ACS, RSC, IEEE, AIP, IOP, APS, AAAS/Science, Nature, Annual Reviews, Taylor & Francis, ECS, AVS, OSA, KPS, PNAS, ... |

Download throughput is automatically throttled to satisfy TDM rate limits.

## Quick start

1. Clone the repository and create a virtual environment:
   ```bash
   pip install uv
   uv sync
   ```
2. Copy `.env.example` to `.env` and fill in the credentials you have available. (See [Configuration](#configuration) for details.)
3. Export your Web of Science list as `savedrecs.xls` and place it next to this README.
4. Run  the following command to download:
   ```bash
   uv run python -m auto_paper_download --savedrecs savedrecs.xls (Optional: Specify xls files for targeted downloads.)
   ```

### (Optional) Enable browser fallback for paywalled publishers

For publishers without a public TDM API (ACS, RSC, IEEE, AIP, IOP, APS, ...), enable
the Playwright-driven fallback. It reuses your existing institutional SSO session, so
paywalled DOIs your university subscribes to become downloadable.

```bash
pip install 'auto-paper-download[browser]'   # or: pip install playwright
playwright install chromium                   # ~150 MB, one-time

uv run python -m auto_paper_download \
  --savedrecs savedrecs.xls \
  --use-browser-fallback
```

First run opens a real Chromium window — log into your institution's SSO once,
and the cookies persist under `~/.cache/auto_paper_download/browser_profile/` for
all subsequent runs.

See [docs/SUPPORTED_PUBLISHERS.md](docs/SUPPORTED_PUBLISHERS.md) for the per-publisher
tier table, and the [paper-download SKILL.md](.claude/skills/paper-download/SKILL.md)
for full configuration of the browser fallback.

## For AI Agents

> **You're an AI agent (Claude / Codex / Copilot / Cursor / Gemini / etc.) and a user
> just pointed you at this repo asking you to "install it as a skill"?** This section
> tells you exactly what to do. Read this whole section before running anything.

### What this repo provides

- A Python package `auto_paper_download` that downloads paper PDFs by DOI.
- A pre-packaged **skill** at `.claude/skills/paper-download/` (SKILL.md + runnable
  scripts) that wraps the package for agent use.
- The skill's `SKILL.md` carries a `name:` + `description:` frontmatter so most agent
  frameworks can auto-route paper-download requests to it.

### Where to install the skill (pick ONE that matches your framework)

```bash
git clone https://github.com/jxtse/auto-paper-harvester.git
cd auto-paper-harvester

# Pick the install destination that matches your agent framework:

# Claude Code (CLI or VS Code extension):
cp -r .claude/skills/paper-download ~/.claude/skills/

# Claude Agent SDK (auto-discovers ~/.claude/skills/):
cp -r .claude/skills/paper-download ~/.claude/skills/

# Codex CLI:
cp -r .claude/skills/paper-download ~/.codex/skills/

# Copilot CLI / VS Code agent (project-local):
mkdir -p .github/skills && cp -r .claude/skills/paper-download .github/skills/

# OpenClaw / other frameworks that auto-discover ~/.openclaw/skills/:
cp -r .claude/skills/paper-download ~/.openclaw/skills/

# Project-local (always works, framework-agnostic):
mkdir -p .agents/skills && cp -r .claude/skills/paper-download .agents/skills/
```

### Then install the runtime deps

The skill protocol doesn't manage Python deps. After copying the skill:

```bash
cd <where-you-copied-the-skill>/paper-download   # or the cloned repo root

# Core: API/OA pipeline only
pip install requests       # this is the only hard dep

# Recommended: enable Playwright browser fallback for paywalled publishers
#   (ACS / RSC / IEEE / AIP / IOP / APS / Annual Reviews / Taylor & Francis / ...)
pip install playwright
playwright install chromium     # ~150 MB, one-time
```

Or install everything via the project's optional extras:

```bash
pip install 'auto-paper-download[browser]' && playwright install chromium
```

### Then configure credentials (one-time)

The skill reads from a `.env` file in the **current working directory** (whatever cwd
the agent runs commands in). Copy the template and fill in what you have:

```bash
cp .env.example .env
# Then edit .env. At minimum set ONE of CROSSREF_MAILTO / OPENALEX_MAILTO to a
# real email address (their public APIs require this for polite-pool access).
# Other credentials (WILEY_TDM_TOKEN, ELSEVIER_API_KEY, SPRINGER_API_KEY,
# UNPAYWALL_EMAIL) are all optional — missing ones just disable that path.
```

If the user hasn't set any creds: **don't fail silently**. The skill will warn that
publishers are disabled — surface that warning to the user verbatim and ask them to
edit `.env`.

### How to invoke the skill

The skill exposes two runnable entry points:

```bash
# Single DOI
python <skill_dir>/scripts/download_by_doi.py --doi <DOI> [--use-browser-fallback]

# Multiple DOIs (flag-repeat or file)
python <skill_dir>/scripts/download_multiple_dois.py \
  --doi-file dois.txt \
  [--resume] [--batch-size N] [--use-browser-fallback]
```

Or (if the user wants the original WoS bulk path):

```bash
uv run python -m auto_paper_download --savedrecs savedrecs.xls [--use-browser-fallback]
```

### Decisions you should make automatically

1. **Enable `--use-browser-fallback`?** Yes, if the DOI list contains any of these
   prefixes: `10.1021` (ACS), `10.1039` (RSC), `10.1126` (Science), `10.1109` (IEEE),
   `10.1063` (AIP), `10.1088` (IOP), `10.1103` (APS), `10.1146` (Annual Reviews),
   `10.1080` (Taylor & Francis). These publishers have **no public TDM API**, so
   without browser fallback they always fail.
2. **Recommend `--resume` + `--batch-size`** for any DOI file with >100 entries —
   protects against network drops.
3. **Don't enable `--overwrite`** unless the user asked for it. PDFs already on disk
   should be cached.
4. **First run with `--use-browser-fallback`** will open a real Chromium window for
   institutional SSO. Warn the user before launching: *"A browser window will open;
   please complete your university login once and the cookies will be cached."*

### Pre-flight checklist (run through this before downloading)

1. **Is the user's institution affiliated with the publishers in the DOI list?** Browser
   fallback only works for content their institution actually subscribes to. For pure
   open-access lists, browser fallback adds nothing over Unpaywall.
2. **Are the DOIs valid?** Format check: each line should match `10.\d{4,9}/.+`. The
   skill silently skips malformed DOIs.
3. **Did the user expose the output dir?** Default is `./downloads/pdfs/`. If running
   on the user's machine, confirm that's where they want files.

### See also

- [.claude/skills/paper-download/SKILL.md](.claude/skills/paper-download/SKILL.md) —
  the full agent runbook (trigger phrases, output layout, troubleshooting).
- [docs/SUPPORTED_PUBLISHERS.md](docs/SUPPORTED_PUBLISHERS.md) — per-publisher
  routing + which tier each one is in.

## Automated Workflow with Claude Code

Use Claude Code to streamline end-to-end literature tasks: search papers first, then download PDFs automatically.

1) Research and discovery
- Open Claude Code’s AI Research Assistant to refine topics, generate queries, and plan search strategies.
- Use the Semantic Scholar MCP tool to search, filter, and collect candidate papers.
- Extract DOIs from results and save them to a text file (one DOI per line), for example `dois.txt`:
  ```text
  10.1038/s41586-020-2649-2
  10.1039/d2nr01648f
  10.1021/acsabm.2c01041
  ```

2) Automated PDF download via Project Skills
- This repository includes a Project Skill under `.claude/skills/paper-download` with two helper scripts that invoke `auto_paper_download`:
  - Single DOI:
    ```bash
    python .claude/skills/paper-download/scripts/download_by_doi.py --doi 10.1038/s41586-020-2649-2 --verbose
    ```
  - Multiple DOIs (repeatable flags or a file):
    ```bash
    # Repeatable flags
    python .claude/skills/paper-download/scripts/download_multiple_dois.py \
      --doi 10.1038/s41586-020-2649-2 \
      --doi 10.1039/d2nr01648f \
      --verbose

    # From a file
    python .claude/skills/paper-download/scripts/download_multiple_dois.py --doi-file ./dois.txt --delay 1.5 --verbose
    ```
- Outputs are saved under `downloads/pdfs/<doi-slug>/`; supplementary PDFs (if detected) are stored alongside the main PDF.

## Performance

- High throughput while respecting publisher Text & Data Mining (TDM) limits. With the default `--delay 1.5s`, theoretical capacity is ~40 PDFs/min; at `--delay 1.0s` (the code enforces a minimum of 1.0s per file for compliance), theoretical capacity is ~60 PDFs/min. Real-world values vary with network/API latency.
- Strong success rates: with OpenAlex/Crossref enabled and `UNPAYWALL_EMAIL` fallback, mixed DOI sets typically achieve close to 90% overall success; individual publishers commonly reach 88–95% when credentials are configured.

### Why it performs well

- Precise routing: DOIs are classified quickly to Wiley/Elsevier/Springer/Crossref, minimizing futile attempts.
- Rate conservation: batch execution enforces `≥ 1.0s/file` throttling, avoiding bans and 429/403 responses.
- OA fallback: when publisher or Crossref/OpenAlex cannot serve a PDF, Unpaywall is automatically attempted to boost success.
- SI capture: after PDF download, DOI landing pages are scanned for supplementary links (PDF-only) to collect key assets in one shot.
- Robust logging: clear per-DOI download plan and summary help you diagnose issues and re-run efficiently.

## Configuration

1. Export `savedrecs.xls` from Web of Science and place it in the project root (or pass a
   custom path via `--savedrecs`).
2. Provide the required credentials/contact details via environment variables or `.env`:
   ```ini
   WILEY_TDM_TOKEN=...
   ELSEVIER_API_KEY=...
   SPRINGER_API_KEY=...        # optional, only used for open-access items
   CROSSREF_MAILTO=you@example.com
   OPENALEX_MAILTO=you@example.com
   UNPAYWALL_EMAIL=you@example.com      # optional, enables Unpaywall OA fallback
   CROSSREF_REQUEST_DELAY=4.0           # optional, seconds between Crossref requests
   WILEY_REQUEST_DELAY=2.5              # optional, seconds between Wiley requests
   ```
   - Missing credentials simply exclude the corresponding publisher.
   - At least one `mailto` is required for Crossref/OpenAlex (polite requests policy).
   - Set `UNPAYWALL_EMAIL` to enable an Unpaywall open-access fallback when publisher/OpenAlex sources cannot serve a PDF.
   - Use `CROSSREF_REQUEST_DELAY` to throttle Crossref PDF fetches (default 4 s) and ease Cloudflare rate limits.
   - Use `WILEY_REQUEST_DELAY` to pace Wiley API calls (default 2.5 s) and avoid rate-limit faults.
   - Springer returns open access records only; paywalled content still needs manual access.
The utility automatically reads the local `.env` file before resolving environment
variables.

## Usage

```bash
uv run python -m auto_paper_download --verbose
```

Common options:
- `--savedrecs`: one or more absolute or relative paths to Web of Science exports (defaults to `savedrecs.xls`)
- `--output-dir`: destination root (defaults to `downloads/pdfs`)
- `--max-per-publisher`: cap downloads per publisher, useful for smoke tests
- `--delay`: seconds between requests (defaults to 1.5, enforced minimum 1.0)
- `--overwrite`: re-download files even if they already exist
- `--dry-run`: inspect the detected DOIs and publisher configuration without downloading
- `--use-browser-fallback`: after HTTP/OA paths fail, retry via Playwright + Chromium using your institutional SSO cookies (one-time `pip install 'auto-paper-download[browser]' && playwright install chromium`)
- `--verbose`: emit debug logs for troubleshooting

During a normal run the tool prints a download plan indicating how many DOIs will be
fetched per publisher. Missing credentials or API keys are reported and the associated
publishers are skipped instead of aborting the session.

After the downloads finish, the CLI reports how many PDFs succeeded per publisher together with the corresponding success rate.
Whenever a publisher API or Crossref/OpenAlex cannot serve a PDF, the downloader attempts an Unpaywall open-access fallback when `UNPAYWALL_EMAIL` is configured.

## Supplementary materials

After a PDF finishes downloading, the tool fetches the DOI landing page, looks for
supplement-related links (keywords such as "supplementary", "SI", "supporting
information", etc.), and downloads only links that resolve to PDF files. Non-PDF assets
are ignored so large datasets or archives are not pulled accidentally. Files are named
safely and stored next to the article PDF.

Because supplementary assets vary widely between publishers, the process is best effort:
paywalls, JavaScript-driven pages, or unconventional link structures may prevent automatic
collection. Warnings are logged when an SI download fails.

## Tips

- Non-open access content from Springer, ACS, RSC, and others still requires dedicated
  TDM access or manual retrieval.
- Frequent HTTP 403 / bot-detection responses often mean the publisher needs to safelist
  your IP or issue additional credentials.
- Examine the logs for the exact URL that failed when extending the downloader to new
  publishers.