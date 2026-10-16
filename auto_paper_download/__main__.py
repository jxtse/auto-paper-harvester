"""
Command line interface for the auto-paper-download package.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .clients import DownloadError
from .downloader import DEFAULT_DELAY_SECONDS, download_from_savedrecs, load_env_file
from .ratelimit import Ledger

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
    parser.add_argument(
        "--use-browser-fallback",
        action="store_true",
        help=(
            "After the HTTP/OA pipeline finishes, retry any DOI that failed using a "
            "Playwright-driven Chromium session that reuses your institutional cookies. "
            "Useful for ACS / RSC / IEEE / AIP / IOP / APS publishers that have no "
            "public TDM API. Requires: pip install playwright && playwright install chromium."
        ),
    )
    parser.add_argument(
        "--browser-mode",
        choices=("playwright", "extension"),
        default="playwright",
        help=(
            "Browser used by --use-browser-fallback. 'playwright' launches its own Chromium "
            "profile; 'extension' drives your running Chrome through the Playwright Extension "
            "(needs playwright-cli and PLAYWRIGHT_MCP_EXTENSION_TOKEN). See docs/BROWSER_FALLBACK.md."
        ),
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        help=(
            "SQLite file for persistent daily caps, per-host pacing and block cooldowns "
            "(also AUTO_PAPER_LEDGER). Recommended for any repeated use. See docs/RATE_LIMITS.md."
        ),
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
        marker = " (browser fallback)" if publisher == "BrowserFallback" else ""
        LOGGER.info(
            "  %s%s: %d/%d PDFs succeeded (%.1f%%)",
            publisher,
            marker,
            succeeded,
            total_attempted,
            rate,
        )

    # Surface the residual failures (DOIs that no path could recover) for the user.
    residual: list[tuple[str, str]] = []
    bf_attempted = metrics.get("BrowserFallback", {}).get("attempted", 0)
    if bf_attempted:
        # Browser-fallback was the last chance; its remaining failed_dois are residual.
        for entry in metrics.get("BrowserFallback", {}).get("failed_dois", []) or []:
            residual.append((entry.get("doi", "?"), entry.get("reason", "")))
    else:
        # No browser-fallback run; aggregate API-pipeline failures.
        for publisher, stats in metrics.items():
            if publisher == "BrowserFallback":
                continue
            for entry in stats.get("failed_dois", []) or []:
                residual.append((entry.get("doi", "?"), entry.get("reason", "")))
    if residual:
        LOGGER.info("%d DOI(s) could not be downloaded:", len(residual))
        for doi, reason in residual[:20]:  # cap noise; full list lives in logs/metrics
            LOGGER.info("  - %s  (%s)", doi, reason)
        if len(residual) > 20:
            LOGGER.info("  ... and %d more (run with --verbose for the full list)",
                        len(residual) - 20)


SUBCOMMANDS_HELP = """\
usage: auto-paper-download <command> [options]

commands:
  get DOI [DOI ...]     download papers by DOI (or --file refs.txt with any text containing DOIs)
  ui                    open the local web page: set API keys, connect Chrome, download
  doctor                show which download steps are configured
  skill [--install DIR] print the agent skill (SKILL.md) or install it for your agent
  --savedrecs FILE ...  download every DOI in a Web of Science export (original interface)

Download order for every paper: open access (OpenAlex/Unpaywall/Crossref) ->
publisher TDM API (Elsevier/Wiley/Springer, if a key is set) -> your browser (opt-in).
Run `auto-paper-download <command> -h` for options.
"""


def _settings_home_env() -> None:
    # Keys saved by the web UI apply to every command; a ./.env still wins over them.
    load_env_file()
    from .webui import ENV_FILE

    load_env_file(ENV_FILE)


def _cmd_get(argv: list[str]) -> None:
    from .downloader import NO_LEDGER, download_from_dois, extract_dois_from_text, unresolved_dois

    p = argparse.ArgumentParser(prog="auto-paper-download get",
                                description="Download papers by DOI.")
    p.add_argument("dois", nargs="*", help="DOIs or doi.org links")
    p.add_argument("--file", type=Path, help="text file containing DOIs (any format)")
    p.add_argument("-o", "--output-dir", type=Path, default=Path("papers"))
    p.add_argument("--browser", choices=("extension", "playwright"),
                   help="step 3: retry failures in your Chrome ('extension') or a separate Chromium")
    p.add_argument("--ledger", type=Path, help="rate-limit ledger (default ~/.auto-paper-download/ledger.sqlite)")
    p.add_argument("--no-ledger", action="store_true", help="disable daily caps and pacing (not recommended)")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", help="print a JSON summary (for agents and scripts)")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")
    text = " ".join(a.dois) + ("\n" + a.file.read_text(encoding="utf-8") if a.file else "")
    dois = extract_dois_from_text(text)
    if not dois:
        raise SystemExit("No DOI found. Pass DOIs as arguments or use --file.")
    _settings_home_env()
    from .webui import DEFAULT_LEDGER

    ledger = NO_LEDGER if a.no_ledger else Ledger(
        a.ledger or Path(os.environ.get("AUTO_PAPER_LEDGER") or DEFAULT_LEDGER))
    stream = download_from_dois(dois=dois, output_dir=a.output_dir, overwrite=a.overwrite,
                                dry_run=a.dry_run, load_env=False,
                                use_browser_fallback=bool(a.browser),
                                browser_mode=a.browser or "playwright", ledger=ledger)
    saved = [str(x) for x in stream]
    metrics = getattr(stream, "metrics", {}) or {}
    failed = unresolved_dois(metrics)
    pdfs = [x for x in saved if x.endswith(".pdf")]
    if a.json:
        print(json.dumps({"requested": len(dois), "pdfs": pdfs, "other_files": len(saved) - len(pdfs),
                          "failed": failed, "output_dir": str(a.output_dir)}, indent=2))
    else:
        print(f"\n{len(pdfs)}/{len(dois)} PDFs in {a.output_dir}")
        for e in failed:
            print(f"  not downloaded: {e['doi']}  ({str(e.get('reason', ''))[:160]})")


def _cmd_doctor(argv: list[str]) -> None:
    argparse.ArgumentParser(prog="auto-paper-download doctor").parse_args(argv)
    _settings_home_env()
    from .webui import DEFAULT_LEDGER, ENV_FILE, steps_view

    st = steps_view()
    mark = lambda ok: "OK  " if ok else "--  "  # noqa: E731
    print(f"settings file: {ENV_FILE} ({'found' if ENV_FILE.exists() else 'not created yet'})")
    print(mark(st["oa"]) + "1. open access: " + (", ".join(st["oa"]) or
          "set OPENALEX_MAILTO / UNPAYWALL_EMAIL / CROSSREF_MAILTO to any e-mail you read"))
    print(mark(st["tdm"]) + "2. publisher TDM APIs: " + (", ".join(st["tdm"]) or
          "none (optional: ELSEVIER_API_KEY / WILEY_TDM_TOKEN / SPRINGER_API_KEY)"))
    b = st["browser"]
    print(mark(b["ready"]) + "3. your Chrome via Playwright Extension: " + ("ready" if b["ready"] else b["why"]))
    try:
        import playwright  # noqa: F401
        print("OK  3b. separate Chromium (playwright) installed")
    except ImportError:
        print("--  3b. separate Chromium: pip install 'auto-paper-download[browser]' && playwright install chromium")
    ledger = Path(os.environ.get("AUTO_PAPER_LEDGER") or DEFAULT_LEDGER).expanduser()
    if ledger.exists():
        u = Ledger(ledger).usage()
        print(f"today: {u['_global_ok']} PDFs saved; cooling down: "
              + (", ".join(c["host"] for c in u["_cooldown"]) or "none"))


def _cmd_ui(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="auto-paper-download ui")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="do not open a browser tab")
    p.add_argument("-o", "--output-dir", type=Path, help="default download folder")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    from .webui import serve

    serve(port=a.port, open_browser=not a.no_open, output=a.output_dir)


def _cmd_skill(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="auto-paper-download skill",
                                description="Print the agent skill, or install it into a skills folder.")
    p.add_argument("--install", type=Path, metavar="SKILLS_DIR",
                   help="e.g. ~/.claude/skills or ~/.codex/skills; creates SKILLS_DIR/paper-download/SKILL.md")
    a = p.parse_args(argv)
    from importlib import resources

    text = resources.files("auto_paper_download").joinpath("skill/SKILL.md").read_text(encoding="utf-8")
    if not a.install:
        print(text, end="")
        return
    dest = a.install.expanduser() / "paper-download" / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"installed {dest}")


def _utf8_console() -> None:
    # Windows consoles default to a legacy code page (cp1252, cp936...) and raise
    # UnicodeEncodeError on characters outside it (titles, the skill text, non-ASCII paths).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> None:
    _utf8_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"get": _cmd_get, "ui": _cmd_ui, "doctor": _cmd_doctor, "skill": _cmd_skill}
    if argv and argv[0] in commands:
        commands[argv[0]](argv[1:])
        return
    if not argv or argv[0] in ("-h", "--help", "help"):
        if not argv or argv[0] == "help" or not Path("savedrecs.xls").exists():
            print(SUBCOMMANDS_HELP)
            if argv and argv[0] in ("-h", "--help"):
                build_parser().print_help()
            return
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

    # Load .env before building the ledger so AUTO_PAPER_LEDGER / AUTO_PAPER_LIMIT_* in it apply.
    load_env_file()
    ledger = Ledger(args.ledger) if args.ledger else Ledger.from_env()
    downloads: list[Path] = []
    aggregate_metrics: defaultdict[str, dict[str, object]] = defaultdict(
        lambda: {"attempted": 0, "succeeded": 0, "failed_dois": []}
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
                use_browser_fallback=args.use_browser_fallback,
                browser_mode=args.browser_mode,
                ledger=ledger,
            )
            downloaded_paths = list(download_iter)
            downloads.extend(downloaded_paths)
            iter_metrics = getattr(download_iter, "metrics", None)
            if iter_metrics:
                for publisher, stats in iter_metrics.items():
                    entry = aggregate_metrics[publisher]
                    entry["attempted"] = entry.get("attempted", 0) + stats.get("attempted", 0)
                    entry["succeeded"] = entry.get("succeeded", 0) + stats.get("succeeded", 0)
                    entry.setdefault("failed_dois", [])
                    entry["failed_dois"].extend(stats.get("failed_dois", []) or [])
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
