"""
FastMCP server exposing the Auto Paper Harvester functionality as MCP tools.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import FastMCP

from auto_paper_download.clients import DownloadError
from auto_paper_download.downloader import (
    DEFAULT_DELAY_SECONDS,
    DOI_PATTERN,
    download_from_dois,
    extract_dois_from_text,
)

LOGGER = logging.getLogger("auto_paper_download.mcp")

mcp = FastMCP("Auto Paper Harvester MCP")

ENV_KEY_MAP = {
    "wiley_tdm_token": "WILEY_TDM_TOKEN",
    "elsevier_api_key": "ELSEVIER_API_KEY",
    "springer_api_key": "SPRINGER_API_KEY",
    "crossref_mailto": "CROSSREF_MAILTO",
    "openalex_mailto": "OPENALEX_MAILTO",
    "unpaywall_email": "UNPAYWALL_EMAIL",
    "crossref_request_delay": "CROSSREF_REQUEST_DELAY",
    "wiley_request_delay": "WILEY_REQUEST_DELAY",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def _normalize_doi(candidate: str) -> str:
    raw = candidate.strip()
    if not raw:
        raise ValueError("Empty DOI entry.")
    lowered = raw.lower()
    if lowered.startswith("https://doi.org/"):
        raw = raw[16:]
    elif lowered.startswith("http://doi.org/"):
        raw = raw[15:]
    elif lowered.startswith("doi:"):
        raw = raw[4:]
    cleaned = raw.strip()
    if not DOI_PATTERN.fullmatch(cleaned):
        raise ValueError(f"Invalid DOI format: {candidate!r}")
    return cleaned


@dataclass
class CredentialStore:
    values: dict[str, str] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_updated: datetime | None = None

    async def update(self, updates: dict[str, Any], *, clear: bool = False) -> dict[str, str]:
        async with self.lock:
            if clear:
                for key in list(self.values):
                    os.environ.pop(key, None)
                self.values.clear()
            for env_key, value in updates.items():
                if value is None:
                    continue
                if isinstance(value, (int, float)):
                    value = str(value)
                if isinstance(value, str):
                    stripped = value.strip()
                else:
                    stripped = str(value).strip()
                if stripped == "":
                    self.values.pop(env_key, None)
                    os.environ.pop(env_key, None)
                    continue
                self.values[env_key] = stripped
                os.environ[env_key] = stripped
            self.last_updated = datetime.now(timezone.utc)
            return dict(self.values)

    async def apply(self) -> dict[str, str]:
        async with self.lock:
            for key, value in self.values.items():
                os.environ[key] = value
            return dict(self.values)

    async def snapshot(self) -> dict[str, str]:
        async with self.lock:
            return dict(self.values)


credential_store = CredentialStore()


@dataclass
class JobRecord:
    job_id: str
    output_dir: str
    files: list[str]
    metrics: dict[str, dict[str, int]]
    dry_run: bool
    created_at: str


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    async def store(self, record: JobRecord) -> None:
        async with self._lock:
            self._jobs[record.job_id] = record

    async def get(self, job_id: str) -> JobRecord | None:
        async with self._lock:
            return self._jobs.get(job_id)


job_registry = JobRegistry()


def _build_job_id() -> str:
    return uuid.uuid4().hex[:8]


async def _run_download(
    *,
    dois: Iterable[str],
    output_dir: Path,
    delay_seconds: float,
    max_per_publisher: int | None,
    overwrite: bool,
    dry_run: bool,
) -> tuple[list[str], dict[str, dict[str, int]]]:
    await credential_store.apply()

    def runner() -> tuple[list[str], dict[str, dict[str, int]]]:
        stream = download_from_dois(
            dois=dois,
            output_dir=output_dir,
            delay_seconds=delay_seconds,
            max_per_publisher=max_per_publisher,
            overwrite=overwrite,
            dry_run=dry_run,
            load_env=False,
        )
        paths = [str(path) for path in stream]
        metrics = getattr(stream, "metrics", {})  # type: ignore[attr-defined]
        return paths, metrics

    try:
        return await asyncio.to_thread(runner)
    except DownloadError as exc:
        LOGGER.error("Download failed: %s", exc)
        raise RuntimeError(str(exc)) from exc


@mcp.tool
async def configure_credentials(
    wiley_tdm_token: str | None = None,
    elsevier_api_key: str | None = None,
    springer_api_key: str | None = None,
    crossref_mailto: str | None = None,
    openalex_mailto: str | None = None,
    unpaywall_email: str | None = None,
    crossref_request_delay: float | None = None,
    wiley_request_delay: float | None = None,
    clear_existing: bool = False,
) -> dict[str, Any]:
    """
    Register API credentials for downstream download calls.

    Pass an empty string to remove a specific credential. Set ``clear_existing`` to wipe all stored values.
    """
    provided = {
        "wiley_tdm_token": wiley_tdm_token,
        "elsevier_api_key": elsevier_api_key,
        "springer_api_key": springer_api_key,
        "crossref_mailto": crossref_mailto,
        "openalex_mailto": openalex_mailto,
        "unpaywall_email": unpaywall_email,
        "crossref_request_delay": crossref_request_delay,
        "wiley_request_delay": wiley_request_delay,
    }
    updates = {
        ENV_KEY_MAP[name]: value for name, value in provided.items() if name in ENV_KEY_MAP
    }
    stored = await credential_store.update(updates, clear=clear_existing)
    snapshot_masked = {key: _mask(value) for key, value in stored.items()}
    last_updated = credential_store.last_updated.isoformat() if credential_store.last_updated else None
    return {
        "stored_keys": sorted(stored),
        "masked_values": snapshot_masked,
        "total": len(stored),
        "last_updated": last_updated,
    }


@mcp.tool
async def parse_savedrecs(
    savedrecs_payload: str,
    encoding: str = "base64",
) -> dict[str, Any]:
    """
    Extract DOI entries from a Web of Science ``savedrecs`` export provided as text or base64.
    """
    if encoding.lower() == "base64":
        try:
            decoded = base64.b64decode(savedrecs_payload, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Unable to decode base64 payload.") from exc
        text = decoded.decode("latin-1", errors="ignore")
    elif encoding.lower() in {"latin-1", "latin1"}:
        text = savedrecs_payload
    else:
        raise ValueError(f"Unsupported encoding: {encoding}")

    dois = extract_dois_from_text(text)
    return {"doi_count": len(dois), "dois": dois}


@mcp.tool
async def download_papers(
    dois: list[str],
    output_dir: str = "downloads/pdfs",
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    max_per_publisher: int | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    job_id: str | None = None,
) -> dict[str, Any]:
    """
    Download PDFs (and supplementary files) for the supplied DOI list.
    """
    if not isinstance(dois, list):
        raise ValueError("Expected `dois` to be a list of DOI strings.")

    normalized: list[str] = []
    errors: list[str] = []
    for entry in dois:
        try:
            normalized.append(_normalize_doi(entry))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("; ".join(errors))

    if not normalized:
        raise ValueError("No valid DOIs supplied.")

    job = job_id or _build_job_id()
    output_path = Path(output_dir)
    target_dir = (output_path / job).resolve() if job_id else output_path.resolve()

    files, metrics = await _run_download(
        dois=normalized,
        output_dir=target_dir,
        delay_seconds=delay_seconds,
        max_per_publisher=max_per_publisher,
        overwrite=overwrite,
        dry_run=dry_run,
    )

    record = JobRecord(
        job_id=job,
        output_dir=str(target_dir),
        files=files,
        metrics=metrics,
        dry_run=dry_run,
        created_at=_now_iso(),
    )
    await job_registry.store(record)

    return {
        "job_id": job,
        "output_dir": record.output_dir,
        "downloaded_files": files,
        "metrics": metrics,
        "dry_run": dry_run,
        "dois": normalized,
    }


@mcp.tool
async def get_job_summary(job_id: str) -> dict[str, Any]:
    """
    Retrieve the stored summary for a previous ``download_papers`` execution.
    """
    record = await job_registry.get(job_id)
    if not record:
        raise ValueError(f"Job {job_id} not found.")
    return {
        "job_id": record.job_id,
        "output_dir": record.output_dir,
        "file_count": len(record.files),
        "files": record.files,
        "metrics": record.metrics,
        "dry_run": record.dry_run,
        "created_at": record.created_at,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Auto Paper Harvester MCP server.")
    parser.add_argument(
        "--transport",
        choices={"stdio", "http"},
        default="stdio",
        help="Transport used to expose the server (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for the HTTP transport (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the HTTP transport (default: 8000).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()
