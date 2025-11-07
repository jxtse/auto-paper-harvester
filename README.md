# Auto Paper Harvester

This is a command line helper that parses Web of Science `savedrecs.xls`
exports, identifies DOIs, selects the appropriate publisher interface, and downloads the
article PDF together with any Supplementary Information (SI) assets that can be detected
on the landing page. Each article ends up in
`downloads/pdfs/<doi-slug>/` with the main PDF named after the DOI slug,
plus any SI files located during scraping.

Supported sources:
- Wiley Text & Data Mining API
- Elsevier Text & Data Mining API
- Springer Nature Open Access API (open access content only)
- OpenAlex (open access copies)
- Crossref / Unpaywall (fallback when OpenAlex succeeds partially)

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