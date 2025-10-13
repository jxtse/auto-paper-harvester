# Auto Paper Download

`auto-paper-download` is a command line helper that parses Web of Science `savedrecs.xls`
exports, identifies DOIs, selects the appropriate publisher interface, and downloads the
article PDF together with any Supplementary Information (SI) assets that can be detected
on the landing page. Each article ends up in
`downloads/pdfs/<publisher>/<doi-slug>/` with an `article.pdf` plus any SI files.

Supported sources:
- Wiley Text & Data Mining API
- Elsevier Text & Data Mining API
- Springer Nature Open Access API (open access content only)
- OpenAlex (open access copies)
- Crossref (fallback when OpenAlex succeeds partially)

Download throughput is automatically throttled to satisfy TDM rate limits.

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

The utility automatically reads the local `.env` file before resolving environment
variables.

## Usage

```bash
python -m auto_paper_download --verbose
```

Common options:
- `--savedrecs`: absolute or relative path to `savedrecs.xls`
- `--output-dir`: destination root (defaults to `downloads/pdfs`)
- `--max-per-publisher`: cap downloads per publisher, useful for smoke tests
- `--delay`: seconds between requests (defaults to 1.5, enforced minimum 1.0)
- `--overwrite`: re-download files even if they already exist
- `--verbose`: emit debug logs for troubleshooting

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

