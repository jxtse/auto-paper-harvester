import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, MutableMapping, Optional, Sequence
from urllib.parse import quote
import time

import requests
from requests.utils import parse_header_links

from .supplements import download_supplements_for_doi

LOGGER = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "AutoPaperDownload/0.1.0 (+https://github.com/ChemBioHTP/EnzyExtract)"
DEFAULT_CROSSREF_REQUEST_DELAY = 2.0

_SAFE_PATH_CHARS = re.compile(r"[^0-9A-Za-z._-]+")


def _safe_identifier(identifier: str) -> str:
    """
    Collapse characters that Windows filesystems reject into underscores, while preserving dots.
    """
    cleaned = _SAFE_PATH_CHARS.sub("_", identifier)
    cleaned = cleaned.strip("._")
    if not cleaned:
        cleaned = "article"
    return cleaned[:150]


def _article_destination(article_dir: Path, base_name: str) -> Path:
    """
    Resolve the primary PDF path inside ``article_dir`` and migrate legacy article.pdf if present.
    """
    article_dir.mkdir(parents=True, exist_ok=True)
    destination = article_dir / f"{base_name}.pdf"
    legacy = article_dir / "article.pdf"
    if legacy.exists() and not destination.exists():
        try:
            legacy.rename(destination)
        except OSError:  # noqa: PERF203
            LOGGER.debug("Failed to rename legacy article.pdf: %s", legacy, exc_info=True)
    return destination


def _cleanup_article_dir(article_dir: Path) -> None:
    """
    Remove ``article_dir`` if it exists and contains no files after a failed download.
    """
    try:
        if article_dir.is_dir() and not any(article_dir.iterdir()):
            article_dir.rmdir()
    except OSError:  # noqa: PERF203
        LOGGER.debug("Failed to remove empty article directory: %s", article_dir, exc_info=True)


class DownloadError(RuntimeError):
    """Raised when a publisher download or search request fails."""


@dataclass
class ArticleRecord:
    """Minimal metadata returned by the publisher search APIs."""

    title: str
    doi: Optional[str]
    pii: Optional[str] = None
    pmid: Optional[str] = None
    url: Optional[str] = None
    publisher: Optional[str] = None


class WileyClient:
    """
    Wiley Text & Data Mining API client.

    Notes
    -----
    * Documentation: https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining
    * The API token issued by Wiley should be stored in the ``WILEY_TDM_TOKEN`` env var.
    * Wiley's endpoints expect the token in the ``Wiley-TDM-Client-Token`` header.
    * Queries follow Wiley's Lucene-like syntax. Keep them specific (e.g. ``"enzyme kinetics" AND catalysis``).
    """

    BASE_URL = "https://api.wiley.com/onlinelibrary/tdm/v1"

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        session: Optional[requests.Session] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        token = token or os.getenv("WILEY_TDM_TOKEN")
        if not token:
            raise ValueError(
                "WileyClient requires a token. Set WILEY_TDM_TOKEN or pass token= explicitly."
            )

        self._token = token
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Wiley-TDM-Client-Token": token,
                "Accept": "application/json",
                "User-Agent": user_agent,
            }
        )

    def search(
        self,
        *,
        query: str,
        limit: int = 20,
        start: int = 0,
        subject_area: Optional[str] = None,
    ) -> list[ArticleRecord]:
        """
        Search Wiley Online Library for articles matching ``query``.

        Parameters
        ----------
        query:
            Lucene-style query string.
        limit:
            Max number of records to return (Wiley caps this at 100 per request).
        start:
            Offset for pagination.
        subject_area:
            Optional subject filter (see Wiley documentation for accepted values).
        """
        params: dict[str, str | int] = {"q": query, "limit": limit, "offset": start}
        if subject_area:
            params["subject"] = subject_area

        url = f"{self.BASE_URL}/articles"
        LOGGER.debug("Wiley search: %s params=%s", url, params)
        response = self._session.get(url, params=params, timeout=60)
        if response.status_code != requests.codes.ok:
            raise DownloadError(
                f"Wiley search failed ({response.status_code}): {response.text}"
            )

        payload = response.json()
        items = payload.get("items", [])
        records: list[ArticleRecord] = []
        for item in items:
            identifiers = item.get("identifiers", {})
            doi = identifiers.get("doi")
            pii = identifiers.get("pii")
            pmid = identifiers.get("pmid")
            records.append(
                ArticleRecord(
                    title=item.get("title", ""),
                    doi=doi,
                    pii=pii,
                    pmid=pmid,
                    url=item.get("link"),
                    publisher="Wiley",
                )
            )
        return records

    def download_pdf(
        self,
        *,
        doi: str,
        destination: Path,
        overwrite: bool = False,
    ) -> Path:
        """
        Download the PDF for ``doi`` into ``destination``.
        """
        if destination.exists() and not overwrite:
            LOGGER.info("Skipping existing file: %s", destination)
            return destination

        destination.parent.mkdir(parents=True, exist_ok=True)
        url = f"{self.BASE_URL}/articles/{doi}"
        LOGGER.debug("Wiley download: %s -> %s", url, destination)
        headers = {"Accept": "application/pdf"}
        response = self._session.get(url, headers=headers, timeout=120, stream=True)
        if response.status_code != requests.codes.ok:
            raise DownloadError(
                f"Wiley download failed ({response.status_code}): {response.text}"
            )

        with destination.open("wb") as fout:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    fout.write(chunk)
        return destination


class SpringerClient:
    """
    Springer Nature Open Access API client.

    Notes
    -----
    * Documentation: https://dev.springernature.com/docs/quick-start/making-first-api-call/
    * Requires an API key stored in ``SPRINGER_API_KEY`` (or passed via ``api_key``).
    * The API returns metadata that includes pre-signed PDF URLs for open access content.
    """

    METADATA_URL = "https://api.springernature.com/metadata/json"
    OPENACCESS_URL = "https://api.springernature.com/openaccess/json"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        session: Optional[requests.Session] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        api_key = (
            api_key
            or os.getenv("SPRINGER_API_KEY")
            or os.getenv("SPRINGER_NATURE_API_KEY")
        )
        if not api_key:
            raise ValueError(
                "SpringerClient requires an API key. "
                "Set SPRINGER_API_KEY or pass api_key= explicitly."
            )

        self._api_key = api_key
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "application/json",
            }
        )

    def search(
        self,
        *,
        query: str,
        page_size: int = 10,
        start: int = 1,
    ) -> list[ArticleRecord]:
        params = {
            "q": query,
            "p": page_size,
            "s": start,
            "api_key": self._api_key,
        }
        LOGGER.debug("Springer search params=%s", params)
        response = self._session.get(self.METADATA_URL, params=params, timeout=60)
        if response.status_code == requests.codes.unauthorized:
            LOGGER.debug("Springer metadata search unauthorized, retrying with openaccess endpoint.")
            response = self._session.get(self.OPENACCESS_URL, params=params, timeout=60)
        if response.status_code != requests.codes.ok:
            raise DownloadError(
                f"Springer search failed ({response.status_code}): {response.text}"
            )

        payload = response.json()
        records: list[ArticleRecord] = []
        for item in payload.get("records", []):
            doi = item.get("doi")
            url_entries = item.get("url", [])
            pdf_url = self._select_pdf_url(url_entries)
            records.append(
                ArticleRecord(
                    title=item.get("title", ""),
                    doi=doi,
                    url=pdf_url,
                    publisher="Springer",
                )
            )
        return records

    def download_pdf(
        self,
        *,
        doi: str,
        destination: Path,
        overwrite: bool = False,
    ) -> Path:
        if destination.exists() and not overwrite:
            LOGGER.info("Skipping existing file: %s", destination)
            return destination

        record = self._fetch_metadata_for_doi(doi)
        pdf_url = self._select_pdf_url(record.get("url", []))
        if not pdf_url:
            pdf_url = self._fallback_pdf_url(doi)
        if not pdf_url:
            raise DownloadError(f"No PDF URL available for Springer DOI {doi}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.debug("Springer download: %s -> %s", pdf_url, destination)
        response = self._session.get(
            pdf_url, headers={"Accept": "application/pdf"}, timeout=120, stream=True
        )
        if response.status_code != requests.codes.ok:
            raise DownloadError(
                f"Springer download failed ({response.status_code}): {response.text}"
            )

        with destination.open("wb") as fout:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    fout.write(chunk)
        return destination

    def _fetch_metadata_for_doi(self, doi: str) -> dict:
        params = {"q": f"doi:{doi}", "p": 1, "api_key": self._api_key}
        LOGGER.debug("Springer metadata lookup params=%s", params)
        response = self._session.get(self.METADATA_URL, params=params, timeout=60)
        if response.status_code == requests.codes.unauthorized:
            LOGGER.debug("Springer metadata lookup unauthorized, retrying with openaccess endpoint.")
            response = self._session.get(self.OPENACCESS_URL, params=params, timeout=60)
        if response.status_code != requests.codes.ok:
            raise DownloadError(
                f"Springer metadata lookup failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        records = payload.get("records", [])
        if not records:
            raise DownloadError(f"Springer metadata not found for DOI {doi}")
        return records[0]

    @staticmethod
    def _select_pdf_url(url_entries: list[dict]) -> Optional[str]:
        for entry in url_entries:
            if entry.get("format", "").lower() == "pdf" and entry.get("value"):
                return entry["value"]
        return None

    @staticmethod
    def _fallback_pdf_url(doi: str) -> Optional[str]:
        if not doi:
            return None
        return f"https://link.springer.com/content/pdf/{doi}.pdf"


class CrossrefClient:
    """
    Crossref Text & Data Mining helper.

    Notes
    -----
    * Documentation: https://www.crossref.org/documentation/retrieve-metadata/rest-api/text-and-data-mining-for-researchers/
    * Requires a contact email (set ``CROSSREF_MAILTO`` or pass ``mailto=``) so Crossref can reach you.
    * Optionally enforces a safelist of license URLs via ``license_safelist`` or ``CROSSREF_LICENSE_SAFELIST``.
    """

    WORK_URL_TEMPLATE = "https://api.crossref.org/works/{doi}"
    DOI_RESOLVER_TEMPLATE = "https://doi.org/{doi}"
    UNIXSD_ACCEPT = "application/vnd.crossref.unixsd+xml"

    def __init__(
        self,
        *,
        mailto: Optional[str] = None,
        license_safelist: Optional[Sequence[str]] = None,
        session: Optional[requests.Session] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        request_delay: Optional[float] = None,
    ) -> None:
        mailto = mailto or os.getenv("CROSSREF_MAILTO")
        if not mailto:
            raise ValueError(
                "CrossrefClient requires a contact email. "
                "Set CROSSREF_MAILTO or pass mailto= explicitly."
            )

        env_safelist = os.getenv("CROSSREF_LICENSE_SAFELIST")
        safelist = list(license_safelist or [])
        if env_safelist:
            safelist.extend(
                entry.strip() for entry in env_safelist.split(",") if entry.strip()
            )
        self._license_safelist = [entry.lower() for entry in safelist] or None

        self._mailto = mailto
        self._session = session or requests.Session()
        ua_with_contact = f"{user_agent} (mailto:{self._mailto})"
        self._session.headers.update(
            {
                "User-Agent": ua_with_contact,
                "Accept": "application/json",
            }
        )
        delay_env = os.getenv("CROSSREF_REQUEST_DELAY")
        delay_value = request_delay
        if delay_env:
            try:
                delay_value = float(delay_env)
            except ValueError:
                LOGGER.debug("Invalid CROSSREF_REQUEST_DELAY value %s; ignoring.", delay_env)
        if delay_value is None:
            delay_value = DEFAULT_CROSSREF_REQUEST_DELAY
        self._request_delay = max(delay_value, 0.0)

    def download_pdf(
        self,
        *,
        doi: str,
        destination: Path,
        overwrite: bool = False,
    ) -> Path:
        if not doi:
            raise ValueError("CrossrefClient.download_pdf requires a DOI.")

        if destination.exists() and not overwrite:
            LOGGER.info("Skipping existing file: %s", destination)
            return destination

        work = self._fetch_work_metadata(doi)
        if not self._license_allowed(work):
            raise DownloadError(
                f"Crossref license for DOI {doi} not in allowed safelist."
            )

        pdf_url = self._select_pdf_url(work)
        if not pdf_url:
            pdf_url = self._extract_pdf_from_link_header(doi)
        if not pdf_url:
            raise DownloadError(f"No PDF link found via Crossref for DOI {doi}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.debug("Crossref download: %s -> %s", pdf_url, destination)
        self._throttle()
        response = self._session.get(
            pdf_url, headers={"Accept": "application/pdf"}, timeout=120, stream=True
        )
        if response.status_code != requests.codes.ok:
            message = f"Crossref download failed ({response.status_code}) for DOI {doi}"
            if response.status_code == 403 and "Just a moment" in (response.text or ""):
                message = f"Crossref download blocked by Cloudflare (403) for DOI {doi}"
            raise DownloadError(message)

        with destination.open("wb") as fout:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    fout.write(chunk)
        return destination

    def _fetch_work_metadata(self, doi: str) -> dict:
        url = self.WORK_URL_TEMPLATE.format(doi=doi)
        params = {"mailto": self._mailto}
        LOGGER.debug("Crossref metadata lookup %s params=%s", url, params)
        self._throttle()
        response = self._session.get(url, params=params, timeout=60)
        if response.status_code != requests.codes.ok:
            message = f"Crossref metadata lookup failed ({response.status_code}) for DOI {doi}"
            if response.status_code == 403 and "Just a moment" in (response.text or ""):
                message = f"Crossref metadata lookup blocked by Cloudflare (403) for DOI {doi}"
            raise DownloadError(message)
        payload = response.json()
        return payload.get("message", {})

    def _license_allowed(self, work: dict) -> bool:
        if not self._license_safelist:
            return True

        licenses = work.get("license") or []
        if not licenses:
            return False

        now = datetime.now(timezone.utc)
        for entry in licenses:
            url = (entry.get("URL") or "").lower()
            if not url:
                continue
            if not self._is_license_active(entry, now):
                continue
            if any(url.startswith(prefix) for prefix in self._license_safelist):
                return True
        return False

    @staticmethod
    def _is_license_active(entry: dict, now: datetime) -> bool:
        start = entry.get("start")
        if not start:
            return True
        timestamp = start.get("timestamp")
        if timestamp is not None:
            try:
                start_dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
            except Exception:  # noqa: BLE001
                start_dt = None
        else:
            date_parts = start.get("date-parts") if isinstance(start, dict) else None
            start_dt = None
            if date_parts:
                parts = date_parts[0]
                year = parts[0]
                month = parts[1] if len(parts) > 1 else 1
                day = parts[2] if len(parts) > 2 else 1
                try:
                    start_dt = datetime(year, month, day, tzinfo=timezone.utc)
                except ValueError:
                    start_dt = None
        if not start_dt:
            return True
        return start_dt <= now

    @staticmethod
    def _preferred_link(links: list[dict]) -> Optional[str]:
        candidates: list[tuple[int, str]] = []
        for link in links:
            if (
                (link.get("content-type") or "").lower() == "application/pdf"
                and link.get("URL")
            ):
                priority = 0 if link.get("intended-application") == "text-mining" else 1
                candidates.append((priority, link["URL"]))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _select_pdf_url(self, work: dict) -> Optional[str]:
        links = work.get("link") or []
        if not isinstance(links, list):
            return None
        return self._preferred_link(links)

    def _extract_pdf_from_link_header(self, doi: str) -> Optional[str]:
        url = self.DOI_RESOLVER_TEMPLATE.format(doi=doi)
        headers = {"Accept": self.UNIXSD_ACCEPT}
        LOGGER.debug("Crossref link header lookup %s", url)
        try:
            self._throttle()
            response = self._session.head(
                url, headers=headers, timeout=30, allow_redirects=True
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Crossref HEAD request failed: %s", exc)
            return None
        if response.status_code != requests.codes.ok:
            LOGGER.debug(
                "Crossref HEAD returned %s for DOI %s", response.status_code, doi
            )
            return None
        link_header = response.headers.get("Link")
        if not link_header:
            return None
        parsed = parse_header_links(link_header.rstrip(">").replace(">,<", ">, <"))
        pdf_links = [
            entry.get("url")
            for entry in parsed
            if (entry.get("type") or "").lower() == "application/pdf"
        ]
        return pdf_links[0] if pdf_links else None

    def _throttle(self) -> None:
        if self._request_delay > 0:
            time.sleep(self._request_delay)


class OpenAlexClient:
    """
    OpenAlex helper for retrieving open-access PDFs.

    Notes
    -----
    * Documentation: https://docs.openalex.org/how-to-use-the-api/api-overview
    * Requires a polite contact email (``OPENALEX_MAILTO`` or ``mailto=`` argument).
    * Only returns PDFs when OpenAlex reports an open-access location with a usable URL.
    """

    BASE_URL = "https://api.openalex.org/works/"

    def __init__(
        self,
        *,
        mailto: Optional[str] = None,
        session: Optional[requests.Session] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        mailto = mailto or os.getenv("OPENALEX_MAILTO")
        if not mailto:
            raise ValueError(
                "OpenAlexClient requires a contact email. "
                "Set OPENALEX_MAILTO or pass mailto= explicitly."
            )
        self._mailto = mailto
        self._session = session or requests.Session()
        ua_with_contact = f"{user_agent} (mailto:{self._mailto})"
        self._session.headers.update(
            {
                "User-Agent": ua_with_contact,
                "Accept": "application/json",
            }
        )

    def download_pdf(
        self,
        *,
        doi: str,
        destination: Path,
        overwrite: bool = False,
    ) -> Path:
        if not doi:
            raise ValueError("OpenAlexClient.download_pdf requires a DOI.")
        if destination.exists() and not overwrite:
            LOGGER.info("Skipping existing file: %s", destination)
            return destination

        work = self._fetch_work(doi)
        if not self._is_open_access(work):
            raise DownloadError(f"OpenAlex reports DOI {doi} is not open access.")

        pdf_url = self._extract_pdf_url(work)
        if not pdf_url:
            raise DownloadError(f"No open-access PDF URL available via OpenAlex for DOI {doi}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.debug("OpenAlex download: %s -> %s", pdf_url, destination)
        response = self._session.get(
            pdf_url, headers={"Accept": "application/pdf"}, timeout=120, stream=True
        )
        if response.status_code != requests.codes.ok:
            raise DownloadError(
                f"OpenAlex download failed ({response.status_code}): {response.text}"
            )

        with destination.open("wb") as fout:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    fout.write(chunk)
        return destination

    def _fetch_work(self, doi: str) -> dict:
        identifier = quote(f"https://doi.org/{doi}", safe=":/")
        url = f"{self.BASE_URL}{identifier}"
        params = {"mailto": self._mailto}
        LOGGER.debug("OpenAlex metadata lookup %s params=%s", url, params)
        response = self._session.get(url, params=params, timeout=60)
        if response.status_code != requests.codes.ok:
            raise DownloadError(
                f"OpenAlex metadata lookup failed ({response.status_code}): {response.text}"
            )
        return response.json()

    @staticmethod
    def _is_open_access(work: dict) -> bool:
        open_access = work.get("open_access") or {}
        return bool(open_access.get("is_oa"))

    @staticmethod
    def _extract_pdf_url(work: dict) -> Optional[str]:
        candidates: list[str] = []

        def add_location(loc: Optional[dict]) -> None:
            if not isinstance(loc, dict):
                return
            pdf_url = loc.get("pdf_url") or loc.get("url_for_pdf")
            if pdf_url:
                candidates.append(pdf_url)

        add_location(work.get("best_oa_location"))
        for loc in work.get("locations", []):
            add_location(loc)

        return candidates[0] if candidates else None


class ElsevierClient:
    """
    Elsevier Text & Data Mining (TDM) API client.

    Notes
    -----
    * Documentation: https://dev.elsevier.com/tdm.html
    * The REST APIs require an API key in ``ELSEVIER_API_KEY``.
    * Most search endpoints live under https://api.elsevier.com/content/search/sciencedirect
      and accept Scopus/ScienceDirect queries.
    * Use the PII or DOI returned by the search to pull the PDF via the article endpoint.
    """

    SEARCH_URL = "https://api.elsevier.com/content/search/sciencedirect"
    ARTICLE_URL_TEMPLATE = "https://api.elsevier.com/content/article/{identifier_type}/{identifier}"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        session: Optional[requests.Session] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        api_key = api_key or os.getenv("ELSEVIER_API_KEY")
        if not api_key:
            raise ValueError(
                "ElsevierClient requires an API key. "
                "Set ELSEVIER_API_KEY or pass api_key= explicitly."
            )

        self._api_key = api_key
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "X-ELS-APIKey": api_key,
                "Accept": "application/json",
                "User-Agent": user_agent,
            }
        )

    def search(
        self,
        *,
        query: str,
        count: int = 25,
        cursor: Optional[str] = None,
    ) -> tuple[list[ArticleRecord], Optional[str]]:
        """
        Search Elsevier ScienceDirect for ``query``.

        Parameters
        ----------
        query:
            Scopus/ScienceDirect query string (e.g. ``TITLE-ABS-KEY("enzyme catalysis")``).
        count:
            Max number of results per request (Elsevier caps at 25 for most keys).
        cursor:
            Optional cursor for pagination. Use the ``next`` cursor returned from a previous call.

        Returns
        -------
        records, next_cursor
        """
        params: dict[str, str] = {"query": query, "count": str(count)}
        params["cursor"] = cursor or "*"

        LOGGER.debug("Elsevier search params=%s", params)
        response = self._session.get(self.SEARCH_URL, params=params, timeout=60)
        if response.status_code != requests.codes.ok:
            raise DownloadError(
                f"Elsevier search failed ({response.status_code}): {response.text}"
            )

        payload = response.json()
        entries = (
            payload.get("search-results", {})
            .get("entry", [])
        )
        records: list[ArticleRecord] = []
        for entry in entries:
            identifiers = entry.get("prism:doi"), entry.get("pii", entry.get("dc:identifier"))
            doi = identifiers[0]
            pii = identifiers[1]
            pmid = entry.get("pubmed-id")
            url = entry.get("link", [{}])[0].get("@href")
            records.append(
                ArticleRecord(
                    title=entry.get("dc:title", ""),
                    doi=doi,
                    pii=pii,
                    pmid=pmid,
                    url=url,
                    publisher="Elsevier",
                )
            )

        next_cursor = payload.get("search-results", {}).get("cursor", {}).get("@next")
        return records, next_cursor

    def download_pdf(
        self,
        *,
        doi: Optional[str] = None,
        pii: Optional[str] = None,
        destination: Path,
        overwrite: bool = False,
    ) -> Path:
        """
        Download the PDF for the specified DOI or PII.

        Elsevier sometimes rejects DOI downloads for certain content types; in that case
        retry with the PII supplied by the search endpoint.
        """
        if not doi and not pii:
            raise ValueError("download_pdf requires a DOI or PII.")

        if destination.exists() and not overwrite:
            LOGGER.info("Skipping existing file: %s", destination)
            return destination

        destination.parent.mkdir(parents=True, exist_ok=True)
        identifier_type = "doi" if doi else "pii"
        identifier = doi if doi else pii
        url = self.ARTICLE_URL_TEMPLATE.format(
            identifier_type=identifier_type, identifier=identifier
        )
        params = {"httpAccept": "application/pdf"}
        LOGGER.debug("Elsevier download: %s params=%s -> %s", url, params, destination)
        response = self._session.get(url, params=params, timeout=120, stream=True)
        if response.status_code != requests.codes.ok:
            raise DownloadError(
                f"Elsevier download failed ({response.status_code}): {response.text}"
            )

        with destination.open("wb") as fout:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    fout.write(chunk)
        return destination


def batched_download(
    *,
    records: Iterable[ArticleRecord],
    output_root: Path,
    elsevier_client: Optional[ElsevierClient] = None,
    crossref_client: Optional[CrossrefClient] = None,
    openalex_client: Optional[OpenAlexClient] = None,
    springer_client: Optional[SpringerClient] = None,
    wiley_client: Optional[WileyClient] = None,
    overwrite: bool = False,
    delay_seconds: Optional[float] = None,
    raise_on_error: bool = True,
    metrics: Optional[MutableMapping[str, dict[str, int]]] = None,
) -> Iterator[Path]:
    """
    Download a batch of records, routing each entry to the appropriate publisher client.
    """
    supplement_session = requests.Session()
    supplement_session.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)

    for record in records:
        publisher = (record.publisher or "").lower()
        publisher_label = record.publisher or "Unknown"
        metrics_entry: Optional[dict[str, int]] = None
        if metrics is not None:
            metrics_entry = metrics.setdefault(
                publisher_label, {"attempted": 0, "succeeded": 0}
            )
            metrics_entry["attempted"] += 1
        article_dir: Optional[Path] = None
        pdf_downloaded = False
        try:
            if "elsevier" in publisher:
                if not elsevier_client:
                    raise DownloadError("ElsevierClient missing for Elsevier record.")
                identifier = record.doi or record.pii
                if not identifier:
                    raise DownloadError(f"No DOI/PII for Elsevier record: {record}")
                fname = _safe_identifier(identifier)
                article_dir = output_root / fname
                pdf_path = _article_destination(article_dir, fname)
                pdf_path = elsevier_client.download_pdf(
                    doi=record.doi,
                    pii=record.pii,
                    destination=pdf_path,
                    overwrite=overwrite,
                )
                pdf_downloaded = True
                yield pdf_path
                if record.doi:
                    for supplemental in download_supplements_for_doi(
                        doi=record.doi,
                        destination_dir=article_dir,
                        session=supplement_session,
                        overwrite=overwrite,
                        publisher=record.publisher,
                    ):
                        yield supplemental
            elif "wiley" in publisher:
                if not wiley_client:
                    raise DownloadError("WileyClient missing for Wiley record.")
                if not record.doi:
                    raise DownloadError(f"No DOI for Wiley record: {record}")
                fname = _safe_identifier(record.doi)
                article_dir = output_root / fname
                pdf_path = _article_destination(article_dir, fname)
                pdf_path = wiley_client.download_pdf(
                    doi=record.doi,
                    destination=pdf_path,
                    overwrite=overwrite,
                )
                pdf_downloaded = True
                yield pdf_path
                for supplemental in download_supplements_for_doi(
                    doi=record.doi,
                    destination_dir=article_dir,
                    session=supplement_session,
                    overwrite=overwrite,
                    publisher=record.publisher,
                ):
                    yield supplemental
            elif "springer" in publisher:
                if not springer_client:
                    raise DownloadError("SpringerClient missing for Springer record.")
                if not record.doi:
                    raise DownloadError(f"No DOI for Springer record: {record}")
                fname = _safe_identifier(record.doi)
                article_dir = output_root / fname
                pdf_path = _article_destination(article_dir, fname)
                try:
                    pdf_path = springer_client.download_pdf(
                        doi=record.doi,
                        destination=pdf_path,
                        overwrite=overwrite,
                    )
                except DownloadError as exc:
                    message = str(exc).lower()
                    if "metadata not found" in message or "download failed (403" in message:
                        LOGGER.info(
                            "Springer DOI %s 跳过：需订阅访问，手动登录后再获取 PDF。", record.doi
                        )
                        _cleanup_article_dir(article_dir)
                        continue
                    raise
                pdf_downloaded = True
                yield pdf_path
                for supplemental in download_supplements_for_doi(
                    doi=record.doi,
                    destination_dir=article_dir,
                    session=supplement_session,
                    overwrite=overwrite,
                    publisher=record.publisher,
                ):
                    yield supplemental
            elif "crossref" in publisher:
                if not record.doi:
                    raise DownloadError(f"No DOI for Crossref record: {record}")
                fname = _safe_identifier(record.doi)
                article_dir = output_root / fname
                tried: list[Exception] = []
                success = False
                if openalex_client:
                    try:
                        pdf_path = _article_destination(article_dir, fname)
                        pdf_path = openalex_client.download_pdf(
                            doi=record.doi,
                            destination=pdf_path,
                            overwrite=overwrite,
                        )
                        pdf_downloaded = True
                        yield pdf_path
                        for supplemental in download_supplements_for_doi(
                            doi=record.doi,
                            destination_dir=article_dir,
                            session=supplement_session,
                            overwrite=overwrite,
                            publisher=record.publisher,
                        ):
                            yield supplemental
                        success = True
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.debug(
                            "OpenAlexClient failed for %s (%s); falling back to Crossref if possible",
                            record.doi,
                            exc,
                        )
                        tried.append(exc)
                if not success and crossref_client:
                    try:
                        pdf_path = _article_destination(article_dir, fname)
                        pdf_path = crossref_client.download_pdf(
                            doi=record.doi,
                            destination=pdf_path,
                            overwrite=overwrite,
                        )
                        pdf_downloaded = True
                        yield pdf_path
                        for supplemental in download_supplements_for_doi(
                            doi=record.doi,
                            destination_dir=article_dir,
                            session=supplement_session,
                            overwrite=overwrite,
                            publisher=record.publisher,
                        ):
                            yield supplemental
                        success = True
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.debug(
                            "CrossrefClient failed for %s (%s)",
                            record.doi,
                            exc,
                        )
                        tried.append(exc)
                if not success:
                    if tried:
                        raise tried[-1]
                    raise DownloadError(
                        "Neither OpenAlexClient nor CrossrefClient configured for Crossref record."
                    )
            else:
                raise DownloadError(
                    f"Unsupported publisher for record {record.publisher}: {record.title}"
                )
            if metrics_entry and pdf_downloaded:
                metrics_entry["succeeded"] += 1
            if delay_seconds:
                time.sleep(delay_seconds)
        except Exception as exc:  # noqa: BLE001
            if article_dir:
                _cleanup_article_dir(article_dir)
            if isinstance(exc, DownloadError):
                LOGGER.warning(
                    "Skipping %s (%s)：%s",
                    record.doi or record.title,
                    record.publisher,
                    exc,
                )
                if raise_on_error:
                    raise
                continue
            LOGGER.exception("Failed to download %s (%s)", record.title, record.publisher)
            if raise_on_error:
                raise
            continue
