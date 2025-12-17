"""
Download PDFs and supplementary files for DOIs listed in filtered_transition_state_papers.xlsx.

This wraps the auto_paper_download helpers and adds checkpoint/resume support for the
large transition-state spreadsheet.

Usage (run from project root):

    python download_filtered_transition_papers.py \
        --excel filtered_transition_state_papers.xlsx \
        --output-dir downloads/pdfs \
        --delay 1.5 --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

import sys
from pathlib import Path as _Path

# Ensure project root is on sys.path so `auto_paper_download` is importable
_HERE = _Path(__file__).resolve()
for _candidate in [_HERE.parent, *_HERE.parents]:
    if (_candidate / "pyproject.toml").exists() or (_candidate / "auto_paper_download").exists():
        sys.path.insert(0, str(_candidate))
        break

from auto_paper_download.clients import DownloadError  # noqa: E402
from auto_paper_download.downloader import (  # noqa: E402
    DEFAULT_DELAY_SECONDS,
    download_from_dois,
)


def _normalize_doi(raw: object) -> str:
    """Normalize DOI strings and strip resolver prefixes."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    lowered = s.lower()
    if lowered.startswith("http://doi.org/") or lowered.startswith("https://doi.org/"):
        return s.split("doi.org/")[-1].strip()
    if lowered.startswith("http://dx.doi.org/") or lowered.startswith("https://dx.doi.org/"):
        return s.split("dx.doi.org/")[-1].strip()
    return s


def _load_dois_from_excel(
    path: Path,
) -> list[str]:
    """Read DOIs from the spreadsheet."""
    try:
        import pandas as pd  # type: ignore
    except ImportError as exc:  # noqa: BLE001
        raise SystemExit(
            "pandas is required to read Excel input. Install with `pip install pandas openpyxl`."
        ) from exc

    df = pd.read_excel(path)

    doi_column = next((col for col in ("DOI", "DOI.1", "doi", "doi.1") if col in df.columns), None)
    if not doi_column:
        raise SystemExit("Could not find a DOI column in the spreadsheet.")

    dois: list[str] = []
    seen: set[str] = set()
    for raw in df[doi_column].fillna(""):
        doi = _normalize_doi(raw)
        if not doi or doi in seen:
            continue
        seen.add(doi)
        dois.append(doi)
    return dois


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download full-text PDFs and supplementary files from filtered_transition_state_papers.xlsx.",
    )
    parser.add_argument(
        "--excel",
        type=Path,
        default=Path("filtered_transition_state_papers.xlsx"),
        help="Path to the transition state spreadsheet (defaults to filtered_transition_state_papers.xlsx).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("downloads/pdfs"),
        help="Destination root directory (defaults to downloads/pdfs).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Seconds to wait between downloads (min 1.0, default 1.5).",
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
        help="Inspect configuration without downloading any files.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from a checkpoint (created after each DOI).",
    )
    parser.add_argument(
        "--checkpoint-file",
        type=Path,
        help="Optional path to a checkpoint file. Default derives from --excel.",
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


def main(argv: Iterable[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    logging.basicConfig(level=log_level, format=log_format)

    # Persist logs for each run to simplify debugging.
    state_dir = Path("downloads/state")
    log_dir = state_dir / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{args.excel.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # capture full detail to disk
        file_handler.setFormatter(logging.Formatter(log_format))
        logging.getLogger().addHandler(file_handler)
        logging.info("File logging enabled at %s", log_file)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to set up file logging: %s", exc)

    if not args.excel.exists():
        raise SystemExit(f"Excel file not found: {args.excel}")

    dois = _load_dois_from_excel(
        args.excel,
    )
    if not dois:
        raise SystemExit("No DOIs found in the spreadsheet.")

    state_dir.mkdir(parents=True, exist_ok=True)
    default_checkpoint_name = f"{args.excel.stem}.checkpoint.json"
    checkpoint_path = args.checkpoint_file or (state_dir / default_checkpoint_name)
    successes_path = state_dir / (checkpoint_path.stem.replace(".checkpoint", "") + "_successes.txt")
    failures_path = state_dir / (checkpoint_path.stem.replace(".checkpoint", "") + "_failures.txt")

    start_idx = 0
    end_idx = len(dois)
    if args.resume and checkpoint_path.exists():
        try:
            data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            last_idx = int(data.get("last_completed_index", -1))
            start_idx = max(last_idx + 1, 0)
            logging.info("Resuming from checkpoint %s (next index=%d)", checkpoint_path, start_idx)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to read checkpoint %s: %s; starting from 0", checkpoint_path, exc)
            start_idx = 0

    if args.batch_size and args.batch_size > 0:
        start_idx = start_idx if args.resume else args.batch_index * args.batch_size
        end_idx = min(start_idx + args.batch_size, len(dois))
        logging.info(
            "Batch selection: start=%d end=%d size=%d index=%d",
            start_idx,
            end_idx,
            args.batch_size,
            args.batch_index,
        )

    selected = dois[start_idx:end_idx]
    if not selected:
        logging.info("No DOIs selected for this run (start=%d, end=%d).", start_idx, end_idx)
        return

    if args.dry_run:
        logging.info("Dry run for %d DOI(s); no files will be downloaded.", len(selected))
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
        return

    downloads: list[Path] = []
    failures: list[str] = []

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
                with successes_path.open("a", encoding="utf-8") as handle:
                    for p in paths:
                        handle.write(f"{doi}\t{p}\n")
            else:
                failures.append(doi)
                with failures_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{doi}\tNO_OUTPUT\n")
        except DownloadError as exc:
            logging.warning("Download error for DOI %s: %s", doi, exc)
            failures.append(doi)
            with failures_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{doi}\tERROR:{exc}\n")
        finally:
            payload = {
                "last_completed_index": idx,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "total_dois": len(dois),
                "start_index_run": start_idx,
                "end_index_run": end_idx,
            }
            try:
                checkpoint_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:  # noqa: BLE001
                logging.warning("Failed to write checkpoint %s: %s", checkpoint_path, exc)

    if downloads:
        for path in downloads:
            print(path)
        logging.info("Saved %d path(s).", len(downloads))
    else:
        logging.info("No files downloaded.")

    if failures:
        logging.info("%d DOI(s) failed in this run. See: %s", len(failures), failures_path)
        print("\nFAILED_DOIS (also written to %s):" % failures_path)
        for doi in failures:
            print(doi)


if __name__ == "__main__":
    main()
