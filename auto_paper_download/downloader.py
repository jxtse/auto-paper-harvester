"""
Core helpers for extracting DOIs from Web of Science exports and downloading PDFs.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable, Iterator

from .clients import (
    ArticleRecord,
    DownloadError,
    ElsevierClient,
    WileyClient,
    batched_download,
)

LOGGER = logging.getLogger(__name__)

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[\x21-\x7E]+")
WILEY_PREFIXES = ("10.1002", "10.1111")
ELSEVIER_PREFIXES = ("10.1016", "10.1011")  # 10.1011 is rare but reserved by Elsevier
DEFAULT_DELAY_SECONDS = 1.1  # respect the 1 PDF/sec cap with a small safety margin


def load_env_file(path: Path | str = ".env") -> None:
    """Populate ``os.environ`` with key/value pairs from a dotenv-style file."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


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
    return None


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
) -> Iterator[Path]:
    """
    Download PDFs referenced in ``savedrecs.xls`` while honoring publisher rate limits.
    """
    load_env_file()
    dois = extract_dois(savedrecs)
    LOGGER.info("Extracted %d DOIs from %s", len(dois), savedrecs)
    records = records_from_dois(dois)

    if max_per_publisher is not None:
        records = _limit_records_per_publisher(records, max_per_publisher)

    if not records:
        LOGGER.warning("No Wiley or Elsevier DOIs detected in %s", savedrecs)
        return iter(())

    need_wiley = any(rec.publisher == "Wiley" for rec in records)
    need_elsevier = any(rec.publisher == "Elsevier" for rec in records)
    wiley_client = WileyClient() if need_wiley else None
    elsevier_client = ElsevierClient() if need_elsevier else None

    output_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info(
        "Starting downloads: %d Wiley, %d Elsevier",
        sum(1 for r in records if r.publisher == "Wiley"),
        sum(1 for r in records if r.publisher == "Elsevier"),
    )

    enforced_delay = max(delay_seconds, 1.0)
    if enforced_delay > delay_seconds:
        LOGGER.warning(
            "Delay %.2fs is below the 1 PDF/sec limit; enforcing %.2fs instead.",
            delay_seconds,
            enforced_delay,
        )
    try:
        return batched_download(
            records=records,
            output_root=output_dir,
            elsevier_client=elsevier_client,
            wiley_client=wiley_client,
            overwrite=overwrite,
            delay_seconds=enforced_delay,
            raise_on_error=False,
        )
    except DownloadError as exc:
        LOGGER.error("Publisher download failed: %s", exc)
        raise
