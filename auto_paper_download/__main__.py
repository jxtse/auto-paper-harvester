"""
Command line interface for the auto-paper-download package.
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .clients import DownloadError
from .downloader import DEFAULT_DELAY_SECONDS, download_from_savedrecs

LOGGER = logging.getLogger("auto_paper_download.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download publisher PDFs and supplementary files listed in a Web of Science savedrecs.xls export."
        )
    )
    parser.add_argument(
        "--savedrecs",
        type=Path,
        nargs="+",
        default=[Path("savedrecs.xls")],
        help=(
            "One or more Web of Science exports to load (defaults to ./savedrecs.xls). "
            "Provide multiple paths to combine downloads."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("downloads/pdfs"),
        help="Directory where article folders (PDF + SI) will be saved (defaults to downloads/pdfs).",
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect configuration and DOIs without downloading any files.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def _log_success(paths: Iterable[Path]) -> None:
    count = 0
    for count, pdf_path in enumerate(paths, start=1):
        LOGGER.info("Saved %s", pdf_path)
    if count == 0:
        LOGGER.info("No PDFs downloaded.")


def _log_publisher_summary(metrics: dict[str, dict[str, int]]) -> None:
    attempted = [
        (publisher, stats)
        for publisher, stats in metrics.items()
        if stats.get("attempted", 0)
    ]
    if not attempted:
        LOGGER.info("No publisher downloads were attempted; skipping summary.")
        return

    LOGGER.info("Publisher PDF download summary:")
    for publisher, stats in sorted(attempted, key=lambda item: item[0].lower()):
        total_attempted = stats.get("attempted", 0)
        succeeded = stats.get("succeeded", 0)
        rate = (succeeded / total_attempted * 100) if total_attempted else 0.0
        LOGGER.info(
            "  %s: %d/%d PDFs succeeded (%.1f%%)",
            publisher,
            succeeded,
            total_attempted,
            rate,
        )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if args.delay < 0:
        raise SystemExit("Delay must be non-negative.")

    savedrecs_paths = list(args.savedrecs)
    missing = [str(path) for path in savedrecs_paths if not path.exists()]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"savedrecs input file(s) not found: {joined}")

    downloads: list[Path] = []
    aggregate_metrics: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"attempted": 0, "succeeded": 0}
    )
    try:
        for savedrecs_path in savedrecs_paths:
            LOGGER.info("Processing input file %s", savedrecs_path)
            download_iter = download_from_savedrecs(
                savedrecs=savedrecs_path,
                output_dir=args.output_dir,
                delay_seconds=args.delay,
                max_per_publisher=args.max_per_publisher,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
            downloaded_paths = list(download_iter)
            downloads.extend(downloaded_paths)
            iter_metrics = getattr(download_iter, "metrics", None)
            if iter_metrics:
                for publisher, stats in iter_metrics.items():
                    entry = aggregate_metrics[publisher]
                    entry["attempted"] += stats.get("attempted", 0)
                    entry["succeeded"] += stats.get("succeeded", 0)
    except DownloadError as exc:
        LOGGER.error("Download aborted: %s", exc)
        raise SystemExit(1) from exc

    if args.dry_run:
        LOGGER.info("Dry run finished; no files were downloaded.")
        return

    _log_success(downloads)
    _log_publisher_summary(aggregate_metrics)


if __name__ == "__main__":
    main()
