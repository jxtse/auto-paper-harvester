"""
Utilities for discovering and downloading supplementary information assets.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

LOGGER = logging.getLogger(__name__)

SUPPLEMENT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

SUPPORTING_KEYWORDS = (
    "supplement",
    "supporting",
    "si",
    "appendix",
    "additional file",
    "extended data",
    "dataset",
    "extra file",
)

ALLOWED_EXTENSIONS = {".pdf"}


def download_supplements_for_doi(
    *,
    doi: str,
    destination_dir: Path,
    session: Optional[requests.Session] = None,
    overwrite: bool = False,
    max_links: int = 10,
    user_agent: Optional[str] = None,
) -> list[Path]:
    """
    Attempt to discover and download supplementary assets linked from a DOI landing page.

    Returns the list of downloaded file paths (empty if nothing was found).
    """
    if not doi:
        return []

    session = session or requests.Session()
    agent = user_agent or SUPPLEMENT_USER_AGENT
    session.headers.setdefault("User-Agent", agent)

    doi_url = f"https://doi.org/{doi}"
    try:
        response = session.get(
            doi_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            },
            timeout=60,
            allow_redirects=True,
        )
    except requests.RequestException as exc:  # noqa: BLE001
        LOGGER.warning("Failed to load DOI landing page for %s: %s", doi, exc)
        return []

    if response.status_code >= 400:
        LOGGER.warning(
            "DOI landing page lookup failed for %s (%s)", doi, response.status_code
        )
        return []

    base_url = response.url or doi_url
    soup = BeautifulSoup(response.text, "html.parser")
    raw_links = list(_extract_candidate_links(soup, base_url))
    if not raw_links:
        LOGGER.info("No supplementary candidates detected for DOI %s", doi)
        return []

    saved_paths: list[Path] = []
    used_names: set[str] = set()
    destination_dir.mkdir(parents=True, exist_ok=True)

    for index, candidate_url in enumerate(raw_links[:max_links], start=1):
        try:
            path = _download_single_asset(
                url=candidate_url,
                referer=base_url,
                destination_dir=destination_dir,
                session=session,
                overwrite=overwrite,
                used_names=used_names,
                fallback_basename=f"supplementary_{index}",
            )
        except requests.RequestException as exc:  # noqa: BLE001
            LOGGER.warning(
                "Failed to download supplementary asset for %s: %s", doi, exc
            )
            continue
        if path:
            saved_paths.append(path)
    return saved_paths


def _extract_candidate_links(soup: BeautifulSoup, base_url: str) -> Iterable[str]:
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith("mailto:"):
            continue
        absolute_url = urljoin(base_url, href)
        if absolute_url in seen:
            continue
        if _looks_like_supplement(anchor, href):
            seen.add(absolute_url)
            yield absolute_url


def _looks_like_supplement(anchor: Tag, href: str) -> bool:
    text_parts = [anchor.get_text(separator=" ", strip=True)]
    for attr in ("title", "aria-label", "data-title", "data-label", "data-track-label"):
        value = anchor.attrs.get(attr)
        if isinstance(value, str):
            text_parts.append(value)
    text_parts.append(href)
    haystack = " ".join(part for part in text_parts if part).lower()

    if "article" in haystack and "pdf" in haystack and "supp" not in haystack:
        return False

    if any(keyword in haystack for keyword in SUPPORTING_KEYWORDS):
        return True

    parsed = urlparse(href)
    ext = Path(parsed.path).suffix.lower()
    if ext and ext in ALLOWED_EXTENSIONS:
        return True

    return False


def _download_single_asset(
    *,
    url: str,
    referer: str,
    destination_dir: Path,
    session: requests.Session,
    overwrite: bool,
    used_names: set[str],
    fallback_basename: str,
) -> Optional[Path]:
    headers = {"Referer": referer, "Accept": "application/pdf"}
    response = session.get(url, timeout=120, stream=True, headers=headers)
    if response.status_code >= 400:
        LOGGER.warning(
            "Supplementary asset request failed %s (%s)", url, response.status_code
        )
        return None

    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    url_ext = Path(urlparse(url).path).suffix.lower()
    if url_ext != ".pdf" and "pdf" not in content_type:
        LOGGER.debug("Ignoring non-PDF supplementary asset %s (content-type=%s)", url, content_type or "unknown")
        return None

    filename = _select_filename(
        url=url,
        response=response,
        fallback_basename=fallback_basename,
        used_names=used_names,
        force_suffix=".pdf",
    )
    destination = destination_dir / filename
    if destination.exists() and not overwrite:
        LOGGER.info("Skipping existing supplementary file: %s", destination)
        return destination

    with destination.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                handle.write(chunk)
    return destination


def _select_filename(
    *,
    url: str,
    response: requests.Response,
    fallback_basename: str,
    used_names: set[str],
    force_suffix: Optional[str] = None,
) -> str:
    filename = _filename_from_content_disposition(response.headers.get("Content-Disposition", ""))
    if not filename:
        filename = Path(urlparse(url).path).name

    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    extension = Path(filename).suffix.lower()
    if not extension and content_type:
        guessed_ext = mimetypes.guess_extension(content_type)
        if guessed_ext:
            extension = guessed_ext
    if not filename:
        filename = fallback_basename
    filename = _sanitize_filename(filename)
    if extension and not filename.lower().endswith(extension):
        filename = f"{filename}{extension}"
    if force_suffix:
        suffix = force_suffix if force_suffix.startswith(".") else f".{force_suffix}"
        if not filename.lower().endswith(suffix.lower()):
            filename = f"{Path(filename).stem}{suffix}"
    elif not Path(filename).suffix:
        filename = f"{filename}.bin"

    candidate = filename
    counter = 2
    while candidate.lower() in used_names:
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    used_names.add(candidate.lower())
    return candidate


def _filename_from_content_disposition(header_value: str) -> str:
    if not header_value:
        return ""
    match = re.search(r'filename\\*=UTF-8\'\'(?P<value>[^;]+)', header_value)
    if match:
        return match.group("value")
    match = re.search(r'filename="?(?P<value>[^";]+)"?', header_value)
    if match:
        return match.group("value")
    return ""


def _sanitize_filename(candidate: str) -> str:
    cleaned = re.sub(r"[\\\\/:*?\"<>|]", "_", candidate)
    cleaned = cleaned.strip().strip(".")
    return cleaned or "supplementary"
