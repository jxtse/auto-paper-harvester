import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional
import time

import requests

LOGGER = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "AutoPaperDownload/0.1.0 (+https://github.com/ChemBioHTP/EnzyExtract)"


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
    * Wiley's endpoints expect a bearer token in the ``Authorization`` header.
    * Queries follow Wiley's Lucene-like syntax. Keep them specific (e.g. ``"enzyme kinetics" AND catalysis``).
    """

    BASE_URL = "https://onlinelibrary.wiley.com/api/tdm/v1"

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
                "Authorization": f"Bearer {token}",
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
        url = f"{self.BASE_URL}/articles/{doi}/pdf"
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
    wiley_client: Optional[WileyClient] = None,
    overwrite: bool = False,
    delay_seconds: Optional[float] = None,
    raise_on_error: bool = True,
) -> Iterator[Path]:
    """
    Download a batch of records, routing each entry to the appropriate publisher client.
    """
    for record in records:
        publisher = (record.publisher or "").lower()
        try:
            if "elsevier" in publisher:
                if not elsevier_client:
                    raise DownloadError("ElsevierClient missing for Elsevier record.")
                identifier = record.doi or record.pii
                if not identifier:
                    raise DownloadError(f"No DOI/PII for Elsevier record: {record}")
                fname = identifier.replace("/", "_")
                output_path = output_root / "elsevier" / f"{fname}.pdf"
                yield elsevier_client.download_pdf(
                    doi=record.doi,
                    pii=record.pii,
                    destination=output_path,
                    overwrite=overwrite,
                )
            elif "wiley" in publisher:
                if not wiley_client:
                    raise DownloadError("WileyClient missing for Wiley record.")
                if not record.doi:
                    raise DownloadError(f"No DOI for Wiley record: {record}")
                fname = record.doi.replace("/", "_")
                output_path = output_root / "wiley" / f"{fname}.pdf"
                yield wiley_client.download_pdf(
                    doi=record.doi,
                    destination=output_path,
                    overwrite=overwrite,
                )
            else:
                raise DownloadError(
                    f"Unsupported publisher for record {record.publisher}: {record.title}"
                )
            if delay_seconds:
                time.sleep(delay_seconds)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Failed to download %s (%s)", record.title, record.publisher)
            if raise_on_error:
                raise
            continue
