"""
Helper script to download multiple DOIs' PDFs (and supplementary PDFs) using
the auto_paper_download package.

Usage (run from project root):

    python .claude/skills/paper-download/scripts/download_multiple_dois.py --doi 10.1038/s41586-020-2649-2 --doi 10.1002/anie.202100001 --verbose

Or provide a file with one DOI per line:

    python .claude/skills/paper-download/scripts/download_multiple_dois.py --doi-file ./dois.txt

This script respects the same environment configuration as the CLI:
- Reads `.env` automatically via the package loader.
- Saves outputs under `downloads/pdfs/<doi-slug>/` by default.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import sys
from pathlib import Path as _Path

# Ensure project root is on sys.path so `auto_paper_download` is importable
_HERE = _Path(__file__).resolve()
for _candidate in [_HERE.parent, *_HERE.parents]:
    if (_candidate / "pyproject.toml").exists() or (_candidate / "auto_paper_download").exists():
        sys.path.insert(0, str(_candidate))
        break

from auto_paper_download.clients import DownloadError
from auto_paper_download.downloader import (
    DEFAULT_DELAY_SECONDS,
    download_from_dois,
)


def _normalize_doi(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    # Strip common URL prefixes
    lowered = s.lower()
    if lowered.startswith("http://doi.org/") or lowered.startswith("https://doi.org/"):
        return s.split("doi.org/")[-1].strip()
    if lowered.startswith("http://dx.doi.org/") or lowered.startswith("https://dx.doi.org/"):
        return s.split("dx.doi.org/")[-1].strip()
    return s


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download PDFs for multiple DOIs (plus supplementary PDFs) via auto_paper_download."
        )
    )
    parser.add_argument(
        "--doi",
        action="append",
        help="Specify a DOI; repeat this flag to add more DOIs.",
    )
    parser.add_argument(
        "--doi-file",
        type=Path,
        help="Path to a text file containing one DOI per line.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("downloads/pdfs"),
        help="Destination root directory (defaults to downloads/pdfs)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Seconds to wait between downloads (min 1.0, default 1.5)",
    )
    parser.add_argument(
        "--max-per-publisher",
        type=int,
        help="Optional cap on downloads per publisher.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download files even if they already exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect configuration and publisher routing without downloading any files.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    raw_dois: list[str] = []
    if args.doi:
        raw_dois.extend(args.doi)
    if args.doi_file:
        if not args.doi_file.exists():
            raise SystemExit(f"DOI file not found: {args.doi_file}")
        for line in args.doi_file.read_text(encoding="utf-8").splitlines():
            raw_dois.append(line)

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_dois:
        doi = _normalize_doi(raw)
        if not doi:
            continue
        if doi not in seen:
            seen.add(doi)
            normalized.append(doi)

    if not normalized:
        raise SystemExit("At least one DOI is required. Use --doi or --doi-file.")

    try:
        downloads = list(
            download_from_dois(
                dois=normalized,
                output_dir=args.output_dir,
                delay_seconds=args.delay,
                max_per_publisher=args.max_per_publisher,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
        )
    except DownloadError as exc:
        logging.error("Download aborted: %s", exc)
        raise SystemExit(1) from exc

    if args.dry_run:
        logging.info("Dry run finished; no files were downloaded.")
        return

    if not downloads:
        logging.info("No files downloaded.")
        return

    for path in downloads:
        print(path)
    logging.info("Saved %d path(s).", len(downloads))


if __name__ == "__main__":
    main()