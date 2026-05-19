# Supported Publishers

This table reflects the routing logic in `auto_paper_download/publishers.py` and the
selector library in `auto_paper_download/browser_fallback.py`. Each tier describes the
*best* path available; lower tiers may still work as fallbacks.

## Support tiers

| Tier | Meaning |
|---|---|
| `full` | Has a public TDM API; downloads are reliable with credentials configured. |
| `oa_only` | API serves open-access content only; paywalled DOIs fall to OA aggregators. |
| `partial` | Most content reachable via OpenAlex / Unpaywall (OA-leaning publishers). |
| `browser_only` | No public TDM API. Requires `--use-browser-fallback` + institutional cookies. |
| `unsupported` | DOI prefix not recognised; only generic Crossref/OpenAlex/Unpaywall fallback. |

## Publisher matrix

| DOI prefix | Publisher | Family | Tier | Notes |
|---|---|---|---|---|
| `10.1002` | Wiley | `wiley` | `full` | Wiley TDM API (`WILEY_TDM_TOKEN`) |
| `10.1111` | Wiley | `wiley` | `full` | Wiley TDM API (`WILEY_TDM_TOKEN`) |
| `10.1016` | Elsevier | `elsevier` | `full` | Elsevier TDM API (`ELSEVIER_API_KEY`) |
| `10.1011` | Elsevier (legacy) | `elsevier` | `full` | Rare legacy prefix |
| `10.1007` | Springer Nature | `springer` | `oa_only` | Springer OA API; paywalled content not served |
| `10.1186` | Springer (BMC) | `springer` | `oa_only` | BioMed Central via Springer OA API |
| `10.1038` | Nature Portfolio | `nature` | `oa_only` | Nature family — OA via Springer/Unpaywall only |
| `10.1147` | Springer (IBM J Res Dev) | `springer` | `oa_only` | IBM Journal of R&D, now Springer-hosted |
| `10.1021` | ACS | `acs` | `browser_only` | No TDM API; needs institutional browser session |
| `10.1039` | RSC | `rsc` | `browser_only` | RSC OA when available; browser fallback otherwise |
| `10.1126` | AAAS / Science | `science` | `browser_only` | Science family — no public TDM |
| `10.1073` | PNAS | `pnas` | `partial` | OA after 6 months; OpenAlex/Unpaywall first |
| `10.1149` | ECS | `ecs` | `browser_only` | Electrochemical Society |
| `10.1088` | IOP | `iop` | `browser_only` | IOP Publishing — needs SSO |
| `10.1143` | IOP (JJAP) | `iop` | `browser_only` | Japanese Journal of Applied Physics |
| `10.1063` | AIP | `aip` | `browser_only` | AIP loading-page handled by browser strategy |
| `10.1116` | AVS | `avs` | `browser_only` | Listed before journal-name lookup |
| `10.1109` | IEEE | `ieee` | `browser_only` | IEEE Xplore — institutional session |
| `10.1103` | APS | `aps` | `browser_only` | American Physical Society — needs SSO |
| `10.1146` | Annual Reviews | `annualreviews` | `browser_only` | Browser session |
| `10.1080` | Taylor & Francis | `tandfonline` | `browser_only` | Browser session |
| `10.1364` | Optica (OSA) | `osa` | `browser_only` | Partial OA |
| `10.3938` | Korean Physical Society | `kps` | `browser_only` | Limited coverage |
| `10.3762` | Beilstein | `beilstein` | `partial` | All OA; usually via Unpaywall |

## Browser-fallback selectors

`browser_fallback.py` has a per-family CSS selector list (`PUBLISHER_PDF_SELECTORS`).
The current coverage:

| Family | Selectors |
|---|---|
| acs | `a.button--secondary[title='PDF']`, `a[href*='/doi/pdf/']` |
| rsc | `a.btn--primary[href*='articlepdf']`, `a[href$='.pdf']` |
| wiley | `a.pdf-download`, `a[href*='/doi/pdf/']`, `a[href*='/doi/pdfdirect/']` |
| elsevier | `a.pdf-download-btn-link`, `button.PdfEmbed-button`, `a[href*='/pdfft?']` |
| nature | `a[data-track-action='download pdf']`, `a.c-pdf-download__link` |
| science | `a[data-test='pdf-download']`, `a[href*='/doi/pdf/']` |
| ieee | `xpl-pdf-button a`, `a[href*='stamp/stamp.jsp']` |
| aip | `a.pdf-link`, `a[href*='/doi/pdf/']` |
| iop | `a.btn-multi-block[href*='pdf']`, `a[href$='/pdf']` |
| aps | `a[href*='/pdf/']` |
| annualreviews | `a.show-pdf`, `a[href*='/doi/pdf/']` |
| tandfonline | `a.show-pdf`, `a[href*='/doi/pdf/']` |
| ecs | `a[href*='/article/'][href*='pdf']` |
| osa | `a.btn-pdf`, `a[href*='fulltext.cfm?id']` |

For families not listed (e.g. `pnas`, `kps`), the generic selectors take over:
`a[href$='.pdf']`, `a[href*='/pdf']`, `a[href*='download']`,
`button:has-text('Download PDF')`, `a:has-text('Download PDF')`, `a:has-text('PDF')`.

## Known issues

- **Headless mode unreliable for ACS/Wiley SI capture** — the browser fallback runs
  headed by default for these families. Override with `BROWSER_FALLBACK_HEADLESS=1`
  if you must batch in CI.
- **AIP serves a loading page before the PDF link is visible** — the current
  selectors wait for `networkidle`, which usually clears this. If you see `no_link`
  on `10.1063/*` DOIs, raise the per-nav timeout in `browser_fallback.py`.
- **IEEE often requires an extra captcha challenge** even with valid institutional
  cookies. Those are surfaced as `challenge_timeout`.
- **Springer 403 on subscription DOI** — Springer API only serves OA content;
  use `--use-browser-fallback` with the user's institutional session.

## Adding a new publisher

1. Add the DOI prefix to `PUBLISHER_MAP` in
   `auto_paper_download/publishers.py` with a `PublisherInfo(family, handler, support, ...)`.
   - `handler="wiley"` / `"elsevier"` / `"springer"` if the publisher has a TDM API you
     can wire to an existing client.
   - `handler="browser"` otherwise.
2. (For browser-only families) add CSS selectors to `PUBLISHER_PDF_SELECTORS` in
   `auto_paper_download/browser_fallback.py`.
3. Add a row to the matrix above.
4. Quick sanity test:
   ```bash
   python -c "from auto_paper_download.publishers import classify_publisher; print(classify_publisher('10.X/test'))"
   ```
