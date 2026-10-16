"""
Core helpers for extracting DOIs from Web of Science exports and downloading PDFs plus SI assets.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .clients import (
    ArticleRecord,
    DownloadError,
    ElsevierClient,
    CrossrefClient,
    OpenAlexClient,
    UnpaywallClient,
    SpringerClient,
    WileyClient,
    batched_download,
)
from .ratelimit import Ledger, LimitReached
from .publishers import (
    classify_publisher as _classify_publisher_info,
    family_to_legacy_publisher,
    needs_browser_fallback,
)

LOGGER = logging.getLogger(__name__)

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[\x21-\x7E]+")
# Legacy prefix tuples kept for backwards-compat with any external imports.
# The authoritative routing now lives in ``auto_paper_download.publishers``.
WILEY_PREFIXES = ("10.1002", "10.1111")
ELSEVIER_PREFIXES = ("10.1016", "10.1011")
SPRINGER_PREFIXES = ("10.1007", "10.1038", "10.1186", "10.1147")
DEFAULT_DELAY_SECONDS = 1.5  # respect the 1 PDF/sec cap with a small safety margin


def load_env_file(path: Path | str = ".env") -> bool:
    """Populate ``os.environ`` with key/value pairs from a dotenv-style file."""
    env_path = Path(path)
    if not env_path.exists():
        LOGGER.debug("No .env file found at %s", env_path)
        return False

    loaded = 0
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded += 1

    if loaded:
        LOGGER.info("Loaded %d environment variables from %s", loaded, env_path)
    else:
        LOGGER.debug("No new environment variables loaded from %s", env_path)
    return True


BROWSER_BUCKETS = ("BrowserFallback", "RealBrowser")


def unresolved_dois(metrics: dict) -> list[dict]:
    """DOIs that no step delivered, with the last reason, from a stream's ``metrics``.

    HTTP buckets list every paper the HTTP steps missed; when a browser pass ran, its
    bucket is the final word for the papers it tried.
    """
    last: dict[str, dict] = {}
    for name, bucket in metrics.items():
        if name in BROWSER_BUCKETS:
            continue
        for e in bucket.get("failed_dois", []) or []:
            last[e.get("doi", "?")] = e
    for name in BROWSER_BUCKETS:
        bucket = metrics.get(name)
        if not bucket:
            continue
        failed_here = {e.get("doi"): e for e in bucket.get("failed_dois", []) or []}
        for doi in list(last):
            if doi in failed_here:
                last[doi] = failed_here[doi]
            elif bucket.get("attempted"):
                # tried in the browser and not reported as failed: recovered
                last.pop(doi, None)
    return list(last.values())


def extract_dois(savedrecs_path: Path) -> list[str]:
    """
    Parse a Web of Science ``savedrecs.xls`` export and return a de-duplicated DOI list.

    The file is a legacy XLS container. We avoid third-party readers by scanning for DOI
    literals in its UTF-8/Latin-1 payload.
    
    Also supports real Excel files (.xls/.xlsx) with a DOI column.
    """
    # Try to detect if it's a real Excel file by attempting to read with pandas
    try:
        import pandas as pd  # type: ignore
        df = pd.read_excel(savedrecs_path)
        # Look for a column that contains DOIs
        doi_column = None
        for col in df.columns:
            col_lower = str(col).lower().strip()
            # Support various DOI column names
            if col_lower in ('doi', 'dois', 'digital object identifier', 'digitalobjectidentifier', 'doi_url', 'doi url'):
                doi_column = col
                break
            # Also check if column name contains 'doi'
            if 'doi' in col_lower and doi_column is None:
                doi_column = col
        
        if doi_column is not None:
            LOGGER.info("Detected real Excel file with DOI column: %s", doi_column)
            # Extract DOIs from the column
            dois_from_excel = []
            for value in df[doi_column].dropna():
                value_str = str(value).strip()
                # Remove common prefixes like "https://doi.org/" or "http://dx.doi.org/"
                if value_str.startswith(('https://doi.org/', 'http://doi.org/', 'https://dx.doi.org/', 'http://dx.doi.org/')):
                    value_str = value_str.split('doi.org/')[-1]
                # Validate it's a proper DOI format
                if value_str and DOI_PATTERN.match(value_str):
                    dois_from_excel.append(value_str)
                elif value_str:
                    # Try to extract DOI from the string using regex
                    match = DOI_PATTERN.search(value_str)
                    if match:
                        dois_from_excel.append(match.group(0))
            
            if dois_from_excel:
                # Deduplicate while preserving order
                seen: set[str] = set()
                deduplicated = []
                for doi in dois_from_excel:
                    if doi not in seen:
                        seen.add(doi)
                        deduplicated.append(doi)
                LOGGER.info("Extracted %d DOIs from Excel file", len(deduplicated))
                return deduplicated
        else:
            LOGGER.debug("No DOI column found in Excel file. Columns: %s", df.columns.tolist())
    except ImportError:
        LOGGER.debug("pandas not available, falling back to text parsing")
    except Exception as e:
        # If pandas read fails, fall back to text parsing
        LOGGER.debug("Excel read failed, falling back to text parsing: %s", e)
    
    # Fall back to original text-based parsing for Web of Science exports
    text = savedrecs_path.read_text(encoding="latin-1", errors="ignore")
    return extract_dois_from_text(text)


def extract_dois_from_text(text: str) -> list[str]:
    """
    Parse a string payload and return a de-duplicated DOI list using ``DOI_PATTERN``.
    """
    seen: set[str] = set()
    dois: list[str] = []
    for match in DOI_PATTERN.finditer(text):
        candidate = match.group(0).strip()
        while candidate and ord(candidate[-1]) < 32:
            candidate = candidate[:-1]
        # Text from reference lists ends DOIs with punctuation or a closing bracket that
        # is not part of the DOI ("... doi:10.1002/x.1." or "(10.1002/x.1)").
        while candidate and candidate[-1] in ".,;:'\"" or (
            candidate and candidate[-1] in ")]}>" and
            candidate.count({")": "(", "]": "[", "}": "{", ">": "<"}[candidate[-1]])
            < candidate.count(candidate[-1])
        ):
            candidate = candidate[:-1]
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        dois.append(candidate)
    return dois


def classify_publisher(doi: str, journal: str = "") -> str | None:
    """
    Backwards-compatible publisher label resolver used by the existing dispatcher.

    Wraps :func:`auto_paper_download.publishers.classify_publisher` and maps the new,
    expanded publisher families back to the four legacy labels the rest of the pipeline
    still keys on (``Wiley`` / ``Elsevier`` / ``Springer`` / ``Crossref``).

    For publishers that *can* only be downloaded via institutional browser session
    (ACS / RSC / AIP / IEEE / etc.) this returns ``"Crossref"`` so the OA fallback
    chain (Crossref → OpenAlex → Unpaywall) still has a chance. Browser fallback,
    when enabled, takes a second pass on whatever Crossref leaves un-downloaded.
    """
    info = _classify_publisher_info(doi, journal=journal)
    if info is None:
        return None
    return family_to_legacy_publisher(info.family)


def records_from_dois(dois: Iterable[str]) -> list[ArticleRecord]:
    records: list[ArticleRecord] = []
    for doi in dois:
        publisher = classify_publisher(doi)
        if not publisher:
            LOGGER.debug("Skipping DOI %s (unsupported publisher)", doi)
            continue
        records.append(
            ArticleRecord(
                title=f"DOI {doi}",
                doi=doi,
                publisher=publisher,
            )
        )
    return records


def _limit_records_per_publisher(
    records: list[ArticleRecord], max_per_publisher: int
) -> list[ArticleRecord]:
    limited: list[ArticleRecord] = []
    counts: dict[str, int] = {}
    for record in records:
        publisher_key = (record.publisher or "").lower()
        counts.setdefault(publisher_key, 0)
        if counts[publisher_key] >= max_per_publisher:
            continue
        limited.append(record)
        counts[publisher_key] += 1
    return limited


def download_from_savedrecs(
    *,
    savedrecs: Path,
    output_dir: Path,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    max_per_publisher: int | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    use_browser_fallback: bool = False,
    browser_mode: str = "playwright",
    ledger: Optional[Ledger] = None,
  ) -> Iterator[Path]:
      """
      Download PDFs (and any discoverable SI files) referenced in ``savedrecs.xls`` while honoring publisher rate limits.

      When ``dry_run`` is ``True``, the function only reports on the detected DOIs and
      which publishers are configured, without attempting any downloads.

      When ``use_browser_fallback`` is ``True``, DOIs that fail every HTTP/OA route are
      retried in a second pass using a Playwright-driven Chromium session that reuses
      the user's institutional cookies. Requires ``playwright`` to be installed.
      """
      load_env_file()
      dois = extract_dois(savedrecs)
      LOGGER.info("Extracted %d DOIs from %s", len(dois), savedrecs)
      return download_from_dois(
          dois=dois,
          output_dir=output_dir,
          delay_seconds=delay_seconds,
          max_per_publisher=max_per_publisher,
          overwrite=overwrite,
          dry_run=dry_run,
          load_env=False,
          use_browser_fallback=use_browser_fallback,
          browser_mode=browser_mode,
          ledger=ledger,
      )


def download_from_dois(
    *,
    dois: Iterable[str],
    output_dir: Path,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    max_per_publisher: int | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    load_env: bool = True,
    use_browser_fallback: bool = False,
    browser_mode: str = "playwright",
    ledger: Optional[Ledger] = None,
) -> Iterator[Path]:
    """
    Download PDFs for the provided DOI list using the configured publisher clients.

    ``dois`` may be any iterable; it is consumed eagerly so the caller can supply generators.
    Set ``load_env`` to ``False`` when credentials are injected programmatically.

    ``browser_mode`` selects the browser fallback: ``"playwright"`` (own Chromium profile,
    :mod:`browser_fallback`) or ``"extension"`` (your running Chrome via the Playwright
    Extension, :mod:`real_browser`). ``ledger`` enables persistent daily caps, per-host
    pacing and cooldowns (see :mod:`ratelimit`); when omitted, ``AUTO_PAPER_LEDGER`` is used.
    """
    if load_env:
        load_env_file()
    if ledger is None:
        ledger = Ledger.from_env()

    doi_list = list(dois)
    if not doi_list:
        LOGGER.warning("No DOIs supplied for download.")
        return iter(())

    LOGGER.info("Preparing downloads for %d DOI(s).", len(doi_list))
    records = _prepare_records(doi_list, max_per_publisher=max_per_publisher)

    if not records:
        LOGGER.warning("No Wiley, Elsevier, Springer, or Crossref-eligible DOIs detected.")
        return iter(())

    return _execute_download(
        records=records,
        output_dir=output_dir,
        delay_seconds=delay_seconds,
        overwrite=overwrite,
        dry_run=dry_run,
        use_browser_fallback=use_browser_fallback,
        browser_mode=browser_mode,
        ledger=ledger,
    )


def _prepare_records(
    dois: Iterable[str], *, max_per_publisher: int | None = None
) -> list[ArticleRecord]:
    records = records_from_dois(dois)
    if max_per_publisher is not None:
        records = _limit_records_per_publisher(records, max_per_publisher)
    return records


def _execute_download(
    *,
    records: list[ArticleRecord],
    output_dir: Path,
    delay_seconds: float,
    overwrite: bool,
    dry_run: bool,
    use_browser_fallback: bool = False,
    browser_mode: str = "playwright",
    ledger: Optional[Ledger] = None,
) -> Iterator[Path]:
    records = list(records)
    if ledger is not None:
        LOGGER.info("Rate-limit ledger: %s", ledger.path)
    disabled_publishers: list[str] = []

    def _optional(factory, label: str):
        try:
            return factory()
        except ValueError as exc:
            LOGGER.info("%s not configured: %s", label, exc)
            disabled_publishers.append(f"{label}: {exc}")
            return None

    def has_records(publisher_name: str) -> bool:
        return any(rec.publisher == publisher_name for rec in records)

    # Step 1, open access. Every paper is tried here first, whatever its publisher.
    openalex_client: Optional[OpenAlexClient] = _optional(OpenAlexClient, "OpenAlex (OPENALEX_MAILTO)")
    unpaywall_client: Optional[UnpaywallClient] = _optional(UnpaywallClient, "Unpaywall (UNPAYWALL_EMAIL)")
    crossref_client: Optional[CrossrefClient] = _optional(CrossrefClient, "Crossref (CROSSREF_MAILTO)")
    if not (openalex_client or unpaywall_client or crossref_client):
        LOGGER.warning(
            "No open-access source is configured. Set OPENALEX_MAILTO / UNPAYWALL_EMAIL / "
            "CROSSREF_MAILTO (any e-mail address you read) to enable step 1."
        )

    # Step 2, publisher TDM APIs. Optional: without a key the paper simply skips this step.
    wiley_client: Optional[WileyClient] = (
        _optional(WileyClient, "Wiley TDM (WILEY_TDM_TOKEN)") if has_records("Wiley") else None)
    elsevier_client: Optional[ElsevierClient] = (
        _optional(ElsevierClient, "Elsevier TDM (ELSEVIER_API_KEY)") if has_records("Elsevier") else None)
    springer_client: Optional[SpringerClient] = (
        _optional(SpringerClient, "Springer API (SPRINGER_API_KEY)") if has_records("Springer") else None)

    if not records:
        LOGGER.warning(
            "No DOIs to download."
        )
        return iter(())

    counts = Counter(rec.publisher for rec in records if rec.publisher)
    total_planned = sum(counts.values())
    LOGGER.info(
        "Download plan: %d total DOIs (%s)",
        total_planned,
        ", ".join(f"{publisher}={counts[publisher]}" for publisher in sorted(counts)),
    )
    if disabled_publishers:
        LOGGER.info(
            "Sources not configured (those steps are skipped): %s",
            "; ".join(disabled_publishers),
        )

    sample_dois = [rec.doi for rec in records if rec.doi][:5]
    if sample_dois:
        suffix = "..." if len(records) > len(sample_dois) else ""
        LOGGER.info("Example DOIs queued: %s%s", ", ".join(sample_dois), suffix)

    if dry_run:
        LOGGER.info("Dry run requested; skipping all download attempts.")
        return iter(())

    output_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Beginning downloads; files will be stored in %s", output_dir)

    enforced_delay = max(delay_seconds, 1.0)
    if enforced_delay > delay_seconds:
        LOGGER.warning(
            "Delay %.2fs is below the 1 PDF/sec limit; enforcing %.2fs instead.",
            delay_seconds,
            enforced_delay,
        )
    metrics: dict[str, dict[str, int]] = {}
    try:
        generator = batched_download(
            records=records,
            output_root=output_dir,
            elsevier_client=elsevier_client,
            crossref_client=crossref_client,
            openalex_client=openalex_client,
            unpaywall_client=unpaywall_client,
            springer_client=springer_client,
            wiley_client=wiley_client,
            overwrite=overwrite,
            delay_seconds=enforced_delay,
            raise_on_error=False,
            metrics=metrics,
            ledger=ledger,
        )
    except DownloadError as exc:
        LOGGER.error("Publisher download failed: %s", exc)
        raise

    class DownloadStream(Iterator[Path]):
        def __init__(
            self,
            iterator: Iterator[Path],
            stats: dict[str, dict[str, int]],
            *,
            output_dir: Path,
            overwrite: bool,
            records: list[ArticleRecord],
            run_browser_fallback: bool,
            browser_mode: str,
            ledger: Optional[Ledger],
        ):
            self._iterator = iterator
            self.metrics = stats
            self._output_dir = output_dir
            self._overwrite = overwrite
            self._records = records
            self._run_browser_fallback = run_browser_fallback
            self._browser_mode = browser_mode
            self._ledger = ledger
            self._fallback_done = False

        def __iter__(self) -> "DownloadStream":
            return self

        def __next__(self) -> Path:
            try:
                return next(self._iterator)
            except StopIteration:
                # Primary pipeline exhausted; run browser fallback once if requested.
                if self._run_browser_fallback and not self._fallback_done:
                    self._fallback_done = True
                    self._iterator = _iter_browser_fallback(
                        metrics=self.metrics,
                        records=self._records,
                        output_dir=self._output_dir,
                        overwrite=self._overwrite,
                        browser_mode=self._browser_mode,
                        ledger=self._ledger,
                    )
                    return next(self._iterator)
                raise

    return DownloadStream(
        generator,
        metrics,
        output_dir=output_dir,
        overwrite=overwrite,
        records=records,
        run_browser_fallback=use_browser_fallback,
        browser_mode=browser_mode,
        ledger=ledger,
    )


def _iter_browser_fallback(
    *,
    metrics: dict[str, dict[str, int]],
    records: list[ArticleRecord],
    output_dir: Path,
    overwrite: bool,
    browser_mode: str = "playwright",
    ledger: Optional[Ledger] = None,
) -> Iterator[Path]:
    """
    Second-pass downloader: walks every DOI that failed the API/OA pipeline and tries
    to fetch it via :func:`auto_paper_download.browser_fallback.browser_fallback_download`.

    Updates ``metrics`` in-place under a synthetic ``"BrowserFallback"`` bucket so the
    final report still reflects what the fallback recovered.
    """
    # Collect every failed DOI from any publisher bucket
    failed: list[dict[str, str]] = []
    for bucket in metrics.values():
        for entry in bucket.get("failed_dois", []) or []:
            failed.append(entry)

    if not failed:
        LOGGER.info("Browser fallback: nothing to retry (no failed DOIs).")
        return

    # Skip DOIs that were refused by the rate limiter: retrying them in a browser would
    # defeat the purpose of the cap / cooldown.
    failed = [e for e in failed if not str(e.get("reason", "")).startswith("rate_limited")]
    if not failed:
        LOGGER.info("Browser fallback: nothing to retry.")
        return

    if browser_mode == "extension":
        yield from _iter_real_browser(failed=failed, metrics=metrics, output_dir=output_dir,
                                      overwrite=overwrite, ledger=ledger)
        return

    # Lazy import so the package still works without playwright installed
    try:
        from .browser_fallback import browser_fallback_download
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Browser fallback module unavailable: %s", exc)
        return

    from .publishers import classify_publisher as _classify_info

    # Build a DOI → publisher_family map from the original records for selector hints
    doi_to_family: dict[str, str] = {}
    for rec in records:
        if rec.doi:
            info = _classify_info(rec.doi)
            doi_to_family[rec.doi] = info.family if info else "unknown"

    fallback_bucket = metrics.setdefault(
        "BrowserFallback", {"attempted": 0, "succeeded": 0, "failed_dois": []}
    )
    LOGGER.info("Browser fallback: retrying %d failed DOIs", len(failed))

    for entry in failed:
        doi = entry.get("doi")
        if not doi:
            continue
        host = "browser:" + doi.split("/", 1)[0]
        rid = None
        if ledger is not None:
            try:
                rid = ledger.acquire("browser", host)
            except LimitReached as exc:
                fallback_bucket["failed_dois"].append({"doi": doi, "reason": f"rate_limited: {exc}"})
                LOGGER.warning("Browser fallback: rate limit, skipping %s (%s)", doi, exc)
                continue
        fallback_bucket["attempted"] += 1
        family = doi_to_family.get(doi, "unknown")
        # Store each browser-fallback PDF in a dedicated subdir to avoid colliding
        # with the API pipeline's per-DOI directory layout.
        slug_dir = output_dir / "_browser_fallback"
        result = browser_fallback_download(
            doi=doi,
            output_dir=slug_dir,
            publisher_family=family,
            overwrite=overwrite,
        )
        if ledger is not None:
            ledger.record("browser", host, doi, "ok" if result.success else "fail", None, 0,
                          "" if result.success else f"{result.status}: {result.reason}",
                          reservation=rid)
            if result.status == "challenge_timeout":
                ledger.block(host, f"challenge: {result.reason}"[:200])
        if result.success and result.saved_path is not None:
            fallback_bucket["succeeded"] += 1
            LOGGER.info("Browser fallback recovered %s -> %s", doi, result.saved_path)
            yield result.saved_path
        else:
            fallback_bucket["failed_dois"].append({
                "doi": doi,
                "reason": f"{result.status}: {result.reason}",
            })
            LOGGER.info("Browser fallback could not recover %s (%s: %s)",
                        doi, result.status, result.reason)


def _iter_real_browser(
    *,
    failed: list[dict[str, str]],
    metrics: dict[str, dict[str, int]],
    output_dir: Path,
    overwrite: bool,
    ledger: Optional[Ledger],
) -> Iterator[Path]:
    """Browser pass through the user's running Chrome (Playwright Extension).

    Stops the whole pass on the first challenge page that does not clear by itself, so a
    human can look at it instead of the tool hammering a bot wall.
    """
    from .real_browser import RealBrowser

    bucket = metrics.setdefault(
        "RealBrowser", {"attempted": 0, "succeeded": 0, "failed_dois": []}
    )
    browser = RealBrowser(ledger=ledger)
    ok, why = browser.available()
    if not ok:
        LOGGER.warning("Real-browser fallback unavailable: %s", why)
        for entry in failed:
            bucket["failed_dois"].append({"doi": entry["doi"], "reason": f"unavailable: {why}"})
        return
    LOGGER.info("Real-browser fallback: retrying %d failed DOIs in your Chrome", len(failed))
    try:
        for entry in failed:
            doi = entry.get("doi")
            if not doi:
                continue
            result = browser.download(doi, output_dir / "_browser_fallback", overwrite=overwrite)
            if result.status != "rate_limited":
                bucket["attempted"] += 1
            if result.success and result.saved_path is not None:
                bucket["succeeded"] += 1
                LOGGER.info("Real browser recovered %s -> %s", doi, result.saved_path)
                yield result.saved_path
                continue
            bucket["failed_dois"].append({"doi": doi, "reason": f"{result.status}: {result.reason}"})
            LOGGER.info("Real browser could not recover %s (%s: %s)", doi, result.status, result.reason)
            if result.status == "challenge":
                LOGGER.warning("Challenge page did not clear; stopping the browser pass. "
                               "Open the page in Chrome yourself, then rerun.")
                break
    finally:
        browser.detach()
