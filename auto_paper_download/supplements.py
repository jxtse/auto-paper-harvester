"""
Utilities for discovering and downloading supplementary information assets.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Iterable, Optional, Sequence
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
    "suppl",
    "supplemental",
    "supplementary",
    "supporting",
    "supporting information",
    "si",
    "esi",
    "appendix",
    "additional file",
    "additional material",
    "extended data",
    "dataset",
    "extra file",
    "data supplement",
)

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".csv",
}

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/csv",
}

RELATION_TYPES = (
    "is-supplemented-by",
    "has-supplement",
    "has-supplementary-material",
)

RELATION_KEYWORDS = ("supplement", "is-supplemented-by", "supplementary")

CROSSREF_EVENT_API = "https://api.eventdata.crossref.org/v1/events"


def download_supplements_for_doi(
    *,
    doi: str,
    destination_dir: Path,
    session: Optional[requests.Session] = None,
    overwrite: bool = False,
    max_links: int = 10,
    user_agent: Optional[str] = None,
    publisher: Optional[str] = None,
) -> list[Path]:
    """
    Attempt to discover and download supplementary assets linked from a DOI landing page.

    Returns the list of downloaded file paths (empty if nothing was found).

    Discovery order:
    1. Crossref metadata relations (golden path for Dryad/Figshare-style links).
    2. Crossref Event Data (third-party supplement relationships).
    3. R ``suppdata`` via ``rpy2`` (publisher-specific SI downloader).
    4. HTML landing-page scrape (legacy fallback).

    When ``publisher`` is provided, the function can apply publisher-specific handling
    (for example, Wiley landing pages that require authentication) and skip supplementary
    downloads.
    """
    doi = _normalize_doi(doi)
    if not doi:
        return []

    session = session or requests.Session()
    agent = user_agent or SUPPLEMENT_USER_AGENT
    session.headers.setdefault("User-Agent", agent)

    saved_paths: list[Path] = []
    used_names: set[str] = set()
    destination_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1/2: Crossref metadata + Event Data (golden paths)
    doi_url = f"https://doi.org/{doi}"
    meta_links = _discover_supplement_urls_via_crossref(doi)
    event_links = _discover_supplement_urls_via_event_data(doi, session=session)
    all_meta_links = _unique_preserve([*meta_links, *event_links])

    saved_paths.extend(
        _download_candidate_assets(
            links=all_meta_links[:max_links],
            referer=doi_url,
            destination_dir=destination_dir,
            session=session,
            overwrite=overwrite,
            used_names=used_names,
        )
    )

    # Phase 3: R suppdata (publisher-aware bridge) if metadata didn't yield anything
    if not saved_paths and _suppdata_supported(doi, publisher):
        saved_paths.extend(
            _download_via_suppdata(
                doi=doi,
                destination_dir=destination_dir,
                overwrite=overwrite,
                publisher=publisher,
            )
        )
    elif not saved_paths:
        LOGGER.debug(
            "suppdata skipped for %s (publisher=%s not supported by suppdata)",
            doi,
            publisher or "unknown",
        )

    # Phase 4: Landing page scrape fallback (only if nothing else worked)
    if saved_paths and overwrite is False:
        LOGGER.debug(
            "Supplement(s) already found via metadata/suppdata for %s; skipping HTML scrape.",
            doi,
        )
        return saved_paths

    raw_links, base_url = _discover_links_via_landing_page(
        doi=doi,
        session=session,
        publisher=publisher,
    )
    saved_paths.extend(
        _download_candidate_assets(
            links=raw_links[:max_links],
            referer=base_url or doi_url,
            destination_dir=destination_dir,
            session=session,
            overwrite=overwrite,
            used_names=used_names,
            start_index=len(saved_paths) + 1,
        )
    )
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
    for attr in (
        "title",
        "aria-label",
        "data-title",
        "data-label",
        "data-track-label",
        "data-article-title",
    ):
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


def _is_supported_asset(url_ext: str, content_type: str) -> bool:
    if url_ext in ALLOWED_EXTENSIONS:
        return True
    if content_type:
        normalized = content_type.lower()
        if normalized in ALLOWED_CONTENT_TYPES:
            return True
        if "pdf" in normalized:
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
    headers = {
        "Referer": referer,
        "Accept": "application/octet-stream,application/pdf;q=0.9,*/*;q=0.8",
    }
    response = session.get(url, timeout=120, stream=True, headers=headers)
    if response.status_code >= 400:
        LOGGER.warning(
            "Supplementary asset request failed %s (%s)", url, response.status_code
        )
        return None

    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    url_ext = Path(urlparse(url).path).suffix.lower()
    if not _is_supported_asset(url_ext, content_type):
        LOGGER.debug(
            "Ignoring non-supported supplementary asset %s (content-type=%s)",
            url,
            content_type or "unknown",
        )
        return None
    force_suffix = ".pdf" if url_ext == ".pdf" or "pdf" in content_type else None

    filename = _select_filename(
        url=url,
        response=response,
        fallback_basename=fallback_basename,
        used_names=used_names,
        force_suffix=force_suffix,
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


def _discover_links_via_landing_page(
    *, doi: str, session: requests.Session, publisher: Optional[str]
) -> tuple[list[str], Optional[str]]:
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
        return [], None

    base_url = response.url or doi_url
    raw_links: list[str] = []
    if response.status_code >= 400:
        if response.status_code == 403 and publisher and publisher.lower() == "wiley":
            LOGGER.info(
                "Wiley supplementary download skipped for %s: access restricted; manual login required",
                doi,
            )
            return [], base_url
        LOGGER.warning(
            "DOI landing page lookup failed for %s (%s)", doi, response.status_code
        )
        return [], base_url

    soup = BeautifulSoup(response.text, "html.parser")
    raw_links.extend(_extract_candidate_links(soup, base_url))
    if not raw_links:
        LOGGER.info("No supplementary candidates detected for DOI %s", doi)
    return raw_links, base_url


def _download_candidate_assets(
    *,
    links: Sequence[str],
    referer: str,
    destination_dir: Path,
    session: requests.Session,
    overwrite: bool,
    used_names: set[str],
    start_index: int = 1,
) -> list[Path]:
    saved: list[Path] = []
    for offset, candidate_url in enumerate(links, start=start_index):
        try:
            path = _download_single_asset(
                url=candidate_url,
                referer=referer,
                destination_dir=destination_dir,
                session=session,
                overwrite=overwrite,
                used_names=used_names,
                fallback_basename=f"supplementary_{offset}",
            )
        except requests.RequestException as exc:  # noqa: BLE001
            LOGGER.warning("Failed to download supplementary asset for %s: %s", candidate_url, exc)
            continue
        if path:
            saved.append(path)
    return saved


def _discover_supplement_urls_via_crossref(doi: str) -> list[str]:
    try:
        from habanero import Crossref
    except ImportError:
        LOGGER.debug("habanero not installed; skipping Crossref metadata SI lookup for %s", doi)
        return []

    mailto = os.getenv("CROSSREF_MAILTO") or None
    try:
        client = Crossref(mailto=mailto) if mailto else Crossref()
        payload = client.works(ids=doi) or {}
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Crossref metadata lookup failed for %s: %s", doi, exc)
        return []

    message = payload.get("message") or {}
    relations = message.get("relation") or {}
    urls: list[str] = []
    for relation_name in RELATION_TYPES:
        for rel in relations.get(relation_name, []):
            candidate = rel.get("id") or rel.get("url") or rel.get("ID") or rel.get("URL")
            if candidate:
                urls.append(str(candidate).strip())
    if urls:
        LOGGER.info(
            "Crossref relation-based SI link(s) found for %s: %s",
            doi,
            ", ".join(urls),
        )
    return _unique_preserve(urls)


def _discover_supplement_urls_via_event_data(
    doi: str, *, session: requests.Session
) -> list[str]:
    params = {"obj-id": f"https://doi.org/{doi}", "rows": 200}
    try:
        response = session.get(CROSSREF_EVENT_API, params=params, timeout=30)
    except requests.RequestException as exc:  # noqa: BLE001
        LOGGER.debug("Crossref Event Data request failed for %s: %s", doi, exc)
        return []
    if response.status_code >= 400:
        LOGGER.debug("Crossref Event Data %s returned %s", doi, response.status_code)
        return []
    try:
        data = response.json()
    except ValueError:
        return []

    events = (data.get("message") or {}).get("events") or []
    urls: list[str] = []
    for event in events:
        relation_label = str(
            event.get("relation-type")
            or event.get("relation_type_id")
            or event.get("relation_type")
            or ""
        ).lower()
        if relation_label and not any(key in relation_label for key in RELATION_KEYWORDS):
            continue
        subj = str(event.get("subj_id") or "")
        obj = str(event.get("obj_id") or "")
        candidate = _select_event_url(subj, obj, doi)
        if candidate:
            urls.append(candidate)
    if urls:
        LOGGER.info(
            "Crossref Event Data SI link(s) found for %s: %s",
            doi,
            ", ".join(urls),
        )
    return _unique_preserve(urls)


def _select_event_url(subj: str, obj: str, doi: str) -> str:
    for candidate in (subj, obj):
        cleaned = candidate.strip()
        if not cleaned:
            continue
        lower = cleaned.lower()
        if lower.startswith("http") and "doi.org" not in lower:
            return cleaned
    for candidate in (subj, obj):
        cleaned_doi = _normalize_doi(candidate)
        if cleaned_doi and cleaned_doi != doi:
            return f"https://doi.org/{cleaned_doi}"
    return ""


def _download_via_suppdata(
    *, doi: str, destination_dir: Path, overwrite: bool, publisher: Optional[str]
) -> list[Path]:
    try:
        import rpy2.robjects as ro
        from rpy2.robjects.packages import importr
    except ImportError:
        LOGGER.debug("rpy2 not installed; skipping suppdata for %s", doi)
        return []

    try:
        _ = importr("suppdata")
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("R package 'suppdata' unavailable; skipping SI for %s (%s)", doi, exc)
        return []

    destination_dir.mkdir(parents=True, exist_ok=True)

    # Wiley often blocks suppdata and is not officially supported; numeric selector kept for completeness.
    si_literal = "1" if (publisher and "wiley" in (publisher.lower())) else "NA_character_"
    LOGGER.debug("suppdata si parameter for %s set to %s", doi, si_literal)

    r_func = ro.r(
        f"""
        function(doi_value, dest_dir) {{
          old <- getwd()
          on.exit(setwd(old), add = TRUE)
          dir.create(dest_dir, recursive = TRUE, showWarnings = FALSE)
          setwd(dest_dir)
          # ``suppdata`` insists that ``si`` is numeric or character; set it based on publisher.
          si_param <- {si_literal}
          # suppdata writes to the working directory; si = NA lets the package pick relevant items
          suppressMessages(
            suppdata::suppdata(x = doi_value, si = si_param)
          )
        }}
        """
    )
    try:
        result = r_func(doi, str(destination_dir))
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("suppdata failed for %s: %s", doi, exc)
        return []

    paths: list[Path] = []
    for entry in result:
        candidate = Path(str(entry))
        if not candidate.is_absolute():
            candidate = destination_dir / candidate.name
        if candidate.exists():
            paths.append(candidate)
    if paths:
        LOGGER.info("suppdata downloaded %d file(s) for %s", len(paths), doi)
    return paths


def _suppdata_supported(doi: str, publisher: Optional[str]) -> bool:
    """
    Filter DOIs to ones that ``suppdata`` can handle, per package docs.
    """
    lowered = doi.lower()
    pub = (publisher or "").lower()

    # Providers explicitly covered by suppdata docs
    if lowered.startswith("10.1371/"):  # PLOS
        return True
    if lowered.startswith("10.6084/m9.figshare"):  # Figshare
        return True
    if lowered.startswith("10.5061/dryad"):  # Dryad
        return True
    if lowered.startswith("10.7910/"):  # Dataverse
        return True
    if lowered.startswith("10.5281/zenodo"):  # Zenodo
        return True
    if "ncomms" in lowered or lowered.startswith("10.1038/ncomms"):  # Nature Comms legacy
        return True
    if lowered.startswith("10.1890/"):  # ESA journals
        return True

    # Wiley support is documented by suppdata; requires numeric si selector.
    if "wiley" in pub or lowered.startswith("10.1002/"):
        return True

    # Europe PMC fallback: only try for publishers that routinely deposit to PMC
    if any(key in pub for key in ("plos", "biomed", "bmj", "elife", "public library of science")):
        return True

    return False


def _normalize_doi(raw: object) -> str:
    if raw is None:
        return ""
    value = str(raw).strip()
    if not value:
        return ""
    lowered = value.lower()
    for prefix in ("https://doi.org/", "http://doi.org/"):
        if lowered.startswith(prefix):
            return value.split("doi.org/", 1)[-1].strip()
    for prefix in ("https://dx.doi.org/", "http://dx.doi.org/"):
        if lowered.startswith(prefix):
            return value.split("dx.doi.org/", 1)[-1].strip()
    return value


def _unique_preserve(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
    return ordered


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
