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
- Crossref (fallback when OpenAlex succeeds partially)

Download throughput is automatically throttled to satisfy TDM rate limits.

## Quick start

1. Clone the repository and create a virtual environment:
   ```bash
   pip install uv
   uv venv
   .venv\Scripts\activate   # use `source .venv/bin/activate` on macOS/Linux
   pip install -e .
   ```
2. Copy `.env.example` to `.env` and fill in the credentials you have available.
3. Export your Web of Science list as `savedrecs.xls` and place it next to this README.
4. Run a configuration check before downloading:
   ```bash
   auto-paper-download --dry-run --verbose
   ```
   The dry run logs how many DOIs were detected, which publishers are enabled, and
   sample identifiers without downloading anything.
5. Drop the `--dry-run` flag once the summary looks right.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

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
   ```
   - Missing credentials simply exclude the corresponding publisher.
   - At least one `mailto` is required for Crossref/OpenAlex (polite requests policy).
   - Springer returns open access records only; paywalled content still needs manual access.
   - Credentials are optional—any publisher without configuration is skipped with an explanatory log message.

The utility automatically reads the local `.env` file before resolving environment
variables.

## Usage

```bash
python -m auto_paper_download --verbose
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

After the downloads finish, the CLI reports how many PDFs succeeded per publisher together
with the corresponding success rate.

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

## Testing

```bash
pytest
```

