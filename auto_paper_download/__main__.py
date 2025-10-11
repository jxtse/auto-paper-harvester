"""
Command line interface for the auto-paper-download package.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Iterable

from .clients import DownloadError
from .downloader import DEFAULT_DELAY_SECONDS, download_from_savedrecs

LOGGER = logging.getLogger("auto_paper_download.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download publisher PDFs listed in a Web of Science savedrecs.xls export."
        )
    )
    parser.add_argument(
        "--savedrecs",
        type=Path,
        default=Path("savedrecs.xls"),
        help="Path to the savedrecs.xls file (defaults to ./savedrecs.xls).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("downloads/pdfs"),
        help="Directory where PDFs will be saved (defaults to downloads/pdfs).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Seconds to wait between downloads (min 1.0, default 1.1).",
    )
    parser.add_argument(
        "--max-per-publisher",
        type=int,
        help="Optional cap on downloads per publisher (useful for smoke tests).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download PDFs even if they already exist.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def _log_success(paths: Iterable[Path]) -> None:
    count = 0
    for count, pdf_path in enumerate(paths, start=1):
        LOGGER.info("Saved %s", pdf_path)
    if count == 0:
        LOGGER.info("No PDFs downloaded.")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if args.delay < 0:
        raise SystemExit("Delay must be non-negative.")

    savedrecs_path: Path = args.savedrecs
    if not savedrecs_path.exists():
        raise SystemExit(f"savedrecs.xls file not found: {savedrecs_path}")

    try:
        downloads = list(
            download_from_savedrecs(
                savedrecs=savedrecs_path,
                output_dir=args.output_dir,
                delay_seconds=args.delay,
                max_per_publisher=args.max_per_publisher,
                overwrite=args.overwrite,
            )
        )
    except DownloadError as exc:
        LOGGER.error("Download aborted: %s", exc)
        raise SystemExit(1) from exc

    _log_success(downloads)


if __name__ == "__main__":
    main()
