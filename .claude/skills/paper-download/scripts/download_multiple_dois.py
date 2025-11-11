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
import json
from datetime import datetime

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
    # Resilience and batching
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from a checkpoint (created automatically after each successful DOI).",
    )
    parser.add_argument(
        "--checkpoint-file",
        type=Path,
        help="Optional path to a checkpoint file. Default derives from --doi-file.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        help="Process at most this many DOIs in this run.",
    )
    parser.add_argument(
        "--batch-index",
        type=int,
        default=0,
        help="Zero-based batch index when using --batch-size.",
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

    # Derive checkpoint and state paths
    state_dir = Path("downloads/state")
    state_dir.mkdir(parents=True, exist_ok=True)
    default_checkpoint_name = "multiple_dois.checkpoint.json"
    if args.doi_file:
        default_checkpoint_name = f"{args.doi_file.stem}.checkpoint.json"
    checkpoint_path = args.checkpoint_file or (state_dir / default_checkpoint_name)
    successes_path = state_dir / (checkpoint_path.stem.replace(".checkpoint", "") + "_successes.txt")
    failures_path = state_dir / (checkpoint_path.stem.replace(".checkpoint", "") + "_failures.txt")

    # Determine slice via resume or batching
    start_idx = 0
    end_idx = len(normalized)
    if args.resume and checkpoint_path.exists():
        try:
            data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            last_idx = int(data.get("last_completed_index", -1))
            start_idx = max(last_idx + 1, 0)
            logging.info("Resuming from checkpoint %s (next index=%d)", checkpoint_path, start_idx)
        except Exception as e:
            logging.warning("Failed to read checkpoint %s: %s; starting from 0", checkpoint_path, e)
            start_idx = 0
    if args.batch_size and args.batch_size > 0:
        start_idx = start_idx if args.resume else args.batch_index * args.batch_size
        end_idx = min(start_idx + args.batch_size, len(normalized))
        logging.info("Batch selection: start=%d end=%d size=%d index=%d", start_idx, end_idx, args.batch_size, args.batch_index)

    selected = normalized[start_idx:end_idx]
    if not selected:
        logging.info("No DOIs selected for this run (start=%d, end=%d).", start_idx, end_idx)
        return

    downloads: list[Path] = []
    failures: list[str] = []

    # Dry-run mode: plan without writing checkpoint or reports
    if args.dry_run:
        try:
            _ = list(
                download_from_dois(
                    dois=selected,
                    output_dir=args.output_dir,
                    delay_seconds=args.delay,
                    max_per_publisher=args.max_per_publisher,
                    overwrite=args.overwrite,
                    dry_run=True,
                )
            )
        except DownloadError as exc:
            logging.error("Dry run aborted: %s", exc)
            raise SystemExit(1) from exc
        logging.info("Dry run finished for %d DOI(s); no files were downloaded.", len(selected))
        return

    # Process each DOI individually to support checkpointing and per-DOI reporting
    for idx, doi in enumerate(selected, start=start_idx):
        try:
            paths = list(
                download_from_dois(
                    dois=[doi],
                    output_dir=args.output_dir,
                    delay_seconds=args.delay,
                    max_per_publisher=args.max_per_publisher,
                    overwrite=args.overwrite,
                    dry_run=False,
                )
            )
            if paths:
                downloads.extend(paths)
                # Append report
                with successes_path.open("a", encoding="utf-8") as fh:
                    for p in paths:
                        fh.write(f"{doi}\t{p}\n")
            else:
                failures.append(doi)
                with failures_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"{doi}\tNO_OUTPUT\n")
        except DownloadError as exc:
            logging.warning("Download error for DOI %s: %s", doi, exc)
            failures.append(doi)
            with failures_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{doi}\tERROR:{exc}\n")
        finally:
            # Update checkpoint after each DOI
            payload = {
                "last_completed_index": idx,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "total_dois": len(normalized),
                "start_index_run": start_idx,
                "end_index_run": end_idx,
            }
            try:
                checkpoint_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                logging.warning("Failed to write checkpoint %s: %s", checkpoint_path, e)

    if not downloads:
        logging.info("No files downloaded.")
        # Still summarize failures if any
        if failures:
            logging.info("%d DOI(s) failed in this run.", len(failures))
        return

    for path in downloads:
        print(path)
    logging.info("Saved %d path(s).", len(downloads))
    if failures:
        logging.info("%d DOI(s) failed in this run. See: %s", len(failures), failures_path)


if __name__ == "__main__":
    main()