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

LOGGER = logging.getLogger(__name__)

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[\x21-\x7E]+")
WILEY_PREFIXES = ("10.1002", "10.1111")
ELSEVIER_PREFIXES = ("10.1016", "10.1011")  # 10.1011 is rare but reserved by Elsevier
SPRINGER_PREFIXES = ("10.1007", "10.1038", "10.1186")
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


def extract_dois(savedrecs_path: Path) -> list[str]:
    """
    Parse a Web of Science ``savedrecs.xls`` export and return a de-duplicated DOI list.

    The file is a legacy XLS container. We avoid third-party readers by scanning for DOI
    literals in its UTF-8/Latin-1 payload.
    """
    text = savedrecs_path.read_text(encoding="latin-1", errors="ignore")
    seen: set[str] = set()
    dois: list[str] = []
    for match in DOI_PATTERN.finditer(text):
        candidate = match.group(0).strip()
        while candidate and ord(candidate[-1]) < 32:
            candidate = candidate[:-1]
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        dois.append(candidate)
    return dois


def classify_publisher(doi: str) -> str | None:
    lowered = doi.lower()
    if any(lowered.startswith(prefix) for prefix in WILEY_PREFIXES):
        return "Wiley"
    if any(lowered.startswith(prefix) for prefix in ELSEVIER_PREFIXES):
        return "Elsevier"
    if any(lowered.startswith(prefix) for prefix in SPRINGER_PREFIXES):
        return "Springer"
    return "Crossref"


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
) -> Iterator[Path]:
    """
    Download PDFs (and any discoverable SI files) referenced in ``savedrecs.xls`` while honoring publisher rate limits.

    When ``dry_run`` is ``True``, the function only reports on the detected DOIs and
    which publishers are configured, without attempting any downloads.
    """
    load_env_file()
    dois = extract_dois(savedrecs)
    LOGGER.info("Extracted %d DOIs from %s", len(dois), savedrecs)
    records = records_from_dois(dois)

    if max_per_publisher is not None:
        records = _limit_records_per_publisher(records, max_per_publisher)

    if not records:
        LOGGER.warning(
            "No Wiley, Elsevier, Springer, or Crossref-eligible DOIs detected in %s",
            savedrecs,
        )
        return iter(())

    disabled_publishers: list[str] = []

    def has_records(publisher_name: str) -> bool:
        return any(rec.publisher == publisher_name for rec in records)

    def disable_publisher(publisher_name: str, reason: str) -> None:
        nonlocal records
        LOGGER.warning("%s downloads disabled: %s", publisher_name, reason)
        disabled_publishers.append(f"{publisher_name}: {reason}")
        records = [rec for rec in records if rec.publisher != publisher_name]

    wiley_client: Optional[WileyClient] = None
    if has_records("Wiley"):
        try:
            wiley_client = WileyClient()
        except ValueError as exc:
            disable_publisher("Wiley", str(exc))

    elsevier_client: Optional[ElsevierClient] = None
    if has_records("Elsevier"):
        try:
            elsevier_client = ElsevierClient()
        except ValueError as exc:
            disable_publisher("Elsevier", str(exc))

    springer_client: Optional[SpringerClient] = None
    if has_records("Springer"):
        try:
            springer_client = SpringerClient()
        except ValueError as exc:
            disable_publisher("Springer", str(exc))

    crossref_client: Optional[CrossrefClient] = None
    openalex_client: Optional[OpenAlexClient] = None
    if has_records("Crossref"):
        crossref_error: Optional[str] = None
        openalex_error: Optional[str] = None
        try:
            crossref_client = CrossrefClient()
        except ValueError as exc:
            crossref_error = str(exc)
            LOGGER.warning("Crossref downloads disabled: %s", exc)
        try:
            openalex_client = OpenAlexClient()
        except ValueError as exc:
            openalex_error = str(exc)
            LOGGER.warning("OpenAlex downloads disabled: %s", exc)
        if not crossref_client and not openalex_client:
            reason_parts = [part for part in (crossref_error, openalex_error) if part]
            reason = "; ".join(reason_parts) or "no Crossref/OpenAlex credentials available"
            disable_publisher("Crossref", reason)

    unpaywall_client: Optional[UnpaywallClient] = None
    try:
        unpaywall_client = UnpaywallClient()
    except ValueError as exc:
        LOGGER.debug("Unpaywall fallback unavailable: %s", exc)

    if not records:
        LOGGER.warning(
            "No Wiley, Elsevier, Springer, or Crossref DOIs remain after applying configuration checks."
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
            "Publishers skipped due to configuration issues: %s",
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
        )
    except DownloadError as exc:
        LOGGER.error("Publisher download failed: %s", exc)
        raise

    class DownloadStream(Iterator[Path]):
        def __init__(self, iterator: Iterator[Path], stats: dict[str, dict[str, int]]):
            self._iterator = iterator
            self.metrics = stats

        def __iter__(self) -> "DownloadStream":
            return self

        def __next__(self) -> Path:
            return next(self._iterator)

    return DownloadStream(generator, metrics)
