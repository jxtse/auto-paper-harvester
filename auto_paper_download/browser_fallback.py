"""
Playwright-driven browser fallback for DOIs that no API/OA pipeline can serve.

Why this exists
---------------
The primary download path is HTTP-only:
  Publisher TDM API  →  Crossref  →  OpenAlex  →  Unpaywall

That covers ~85–90% of DOIs in a typical materials-science corpus, but leaves a long
tail of paywalled content from publishers without a public TDM API (ACS, RSC, IEEE,
AIP, IOP, APS, …). For those, the most reliable way to get a PDF — short of literally
clicking through the journal site by hand — is to drive a real browser using the
researcher's already-authenticated institutional session.

This module is a thin, cross-platform reimplementation of the strategy used by
``ltczding-gif/ref-downloader``:

  1. Reuse the user's persistent Chromium/Edge profile (so SSO cookies carry over).
  2. Resolve the DOI (``https://doi.org/<DOI>``) and wait for the publisher landing
     page to settle.
  3. Look for the canonical "Download PDF" affordance — first via publisher-family
     specific selectors, then via a generic heuristic.
  4. Capture the resulting PDF response and save it to disk.
  5. If the request bounces to SSO / paywall / CAPTCHA, mark the result as
     ``manual_pending`` so the calling code can surface it without crashing the batch.

Differences from ref-downloader
-------------------------------
- We use Chromium by default (works on macOS / Linux / Windows). Edge can be opted into
  via ``BROWSER_FALLBACK_CHANNEL=msedge``.
- We integrate as a *second pass* after the API pipeline, not as the primary route —
  the API path stays the high-throughput default.
- We don't try to be as clever about per-publisher quirks (yet); the publisher-specific
  selectors are kept deliberately minimal to make it easy to extend.

Optional dependency
-------------------
Playwright is imported lazily so the package still installs/runs without it. If
``playwright`` (or its Chromium driver) is missing, ``browser_fallback_download``
returns immediately with ``available=False`` and lets the caller log a hint.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

LOGGER = logging.getLogger(__name__)

# --- Configuration knobs -----------------------------------------------------

DEFAULT_NAV_TIMEOUT_MS = 45_000
DEFAULT_SETTLE_TIMEOUT_MS = 10_000
DEFAULT_DOWNLOAD_TIMEOUT_MS = 60_000

# Hosts that almost certainly mean we just got bounced to SSO/login.
# Users can extend this via the ``BROWSER_FALLBACK_AUTH_HOSTS`` env var
# (comma-separated). Lower-cased contains-match.
DEFAULT_AUTH_HOST_FRAGMENTS = (
    "login.", "signin.", "auth.", "sso.", "shibboleth", "idp.",
    "openathens", "ezproxy", "wayf.", "fedauth.",
)
DEFAULT_AUTH_URL_FRAGMENTS = (
    "saml", "oauth", "openid", "login?service=", "shibboleth", "wayf",
)

# Per-family CSS selector hints — first hit wins.
# Keep these narrow & well-commented; this is the surface most likely to drift.
PUBLISHER_PDF_SELECTORS: dict[str, Sequence[str]] = {
    "acs": (
        "a.button--secondary[title='PDF']",
        "a[href*='/doi/pdf/']",
    ),
    "rsc": (
        "a.btn--primary[href*='articlepdf']",
        "a[href$='.pdf']",
    ),
    "wiley": (
        "a.pdf-download",
        "a[href*='/doi/pdf/']",
        "a[href*='/doi/pdfdirect/']",
    ),
    "elsevier": (
        "a.pdf-download-btn-link",
        "button.PdfEmbed-button",
        "a[href*='/pdfft?']",
    ),
    "nature": (
        "a[data-track-action='download pdf']",
        "a.c-pdf-download__link",
    ),
    "science": (
        "a[data-test='pdf-download']",
        "a[href*='/doi/pdf/']",
    ),
    "ieee": (
        "xpl-pdf-button a",
        "a[href*='stamp/stamp.jsp']",
    ),
    "aip": (
        "a.pdf-link",
        "a[href*='/doi/pdf/']",
    ),
    "iop": (
        "a.btn-multi-block[href*='pdf']",
        "a[href$='/pdf']",
    ),
    "aps": (
        "a[href*='/pdf/']",
    ),
    "annualreviews": (
        "a.show-pdf",
        "a[href*='/doi/pdf/']",
    ),
    "tandfonline": (
        "a.show-pdf",
        "a[href*='/doi/pdf/']",
    ),
    "ecs": (
        "a[href*='/article/'][href*='pdf']",
    ),
    "osa": (
        "a.btn-pdf",
        "a[href*='fulltext.cfm?id']",
    ),
}

GENERIC_PDF_SELECTORS = (
    "a[href$='.pdf']",
    "a[href*='/pdf']",
    "a[href*='download']",
    "button:has-text('Download PDF')",
    "a:has-text('Download PDF')",
    "a:has-text('PDF')",
)


# --- Result type -------------------------------------------------------------


@dataclass
class BrowserFallbackResult:
    """Outcome of one browser-based download attempt."""

    available: bool                # Is Playwright + a browser installed?
    success: bool = False
    saved_path: Optional[Path] = None
    status: str = "skipped"        # "downloaded" | "auth_redirect" | "no_link" |
                                   # "challenge_timeout" | "skipped" | "error"
    reason: str = ""
    diagnostics: dict = field(default_factory=dict)

    def __bool__(self) -> bool:  # noqa: D401 — readable truth test
        return bool(self.success)


# --- Public entry point ------------------------------------------------------


def browser_fallback_download(
    *,
    doi: str,
    output_dir: Path,
    publisher_family: str = "unknown",
    overwrite: bool = False,
    headless: Optional[bool] = None,
    user_data_dir: Optional[Path] = None,
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
) -> BrowserFallbackResult:
    """
    Try to download the main PDF for ``doi`` via a headed browser session that reuses
    the user's institutional cookies.

    Parameters
    ----------
    doi : str
        The article DOI (without the ``https://doi.org/`` prefix).
    output_dir : Path
        Directory to save the PDF into. Created if absent.
    publisher_family : str
        Family id from :mod:`auto_paper_download.publishers`. Used to pick selectors.
    overwrite : bool
        If False and ``<output_dir>/<doi-slug>.pdf`` exists, skip the download.
    headless : Optional[bool]
        Override headless mode. By default we run headed on ACS/Wiley (their SI
        downloads need a real window) and headless everywhere else when there's no
        DISPLAY. Can also be overridden via ``BROWSER_FALLBACK_HEADLESS``.
    user_data_dir : Optional[Path]
        Where to persist Chromium profile state. Defaults to
        ``$BROWSER_FALLBACK_PROFILE`` or ``~/.cache/auto_paper_download/browser_profile``.
    timeout_ms : int
        Per-navigation timeout. Defaults to 45 s.

    Returns
    -------
    BrowserFallbackResult
        Always returns; never raises for ordinary failures (timeouts, auth bounces,
        missing buttons). Exceptions are caught and returned as ``status='error'``.
    """
    # Honour an explicit env-var opt-out so this can be disabled in CI / batch jobs
    if _is_disabled():
        return BrowserFallbackResult(available=False, status="skipped",
                                     reason="Disabled via BROWSER_FALLBACK_ENABLED=0")

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        LOGGER.info("Playwright not installed; browser fallback unavailable. "
                    "Install with: pip install playwright && playwright install chromium")
        return BrowserFallbackResult(available=False, status="skipped",
                                     reason="playwright not installed")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target_path = _doi_to_pdf_path(doi, output_dir)
    if target_path.exists() and not overwrite:
        return BrowserFallbackResult(
            available=True, success=True, saved_path=target_path,
            status="cached", reason="File already exists; pass overwrite=True to refetch",
        )

    headless_resolved = _resolve_headless(headless, publisher_family)
    profile_dir = Path(user_data_dir or _default_profile_dir())
    profile_dir.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Browser fallback: DOI=%s family=%s headless=%s profile=%s",
                doi, publisher_family, headless_resolved, profile_dir)

    try:
        with sync_playwright() as pw:
            channel = os.environ.get("BROWSER_FALLBACK_CHANNEL") or None  # e.g. "msedge"
            context = pw.chromium.launch_persistent_context(
                str(profile_dir),
                channel=channel,
                headless=headless_resolved,
                accept_downloads=True,
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)

            try:
                # 1. Resolve DOI → publisher landing page
                doi_url = f"https://doi.org/{doi}"
                page.goto(doi_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_load_state("networkidle", timeout=DEFAULT_SETTLE_TIMEOUT_MS)

                landed = page.url
                if _looks_like_auth_redirect(landed):
                    return BrowserFallbackResult(
                        available=True, success=False, status="auth_redirect",
                        reason=f"Bounced to SSO/login at {landed!r}; log in once in this profile and retry",
                        diagnostics={"final_url": landed},
                    )

                # 2. Look for a PDF link/button using publisher-specific selectors.
                # We resolve the first matching locator OUTSIDE expect_download so a
                # missing button returns immediately as ``no_link`` instead of waiting
                # the full download timeout (the previous implementation entered
                # expect_download() before the selector check, so even early returns
                # blocked for ~60s waiting for a download event that never fired).
                selectors = list(PUBLISHER_PDF_SELECTORS.get(publisher_family, ()))
                selectors.extend(GENERIC_PDF_SELECTORS)

                target_locator = _resolve_first_match(page, selectors)
                if target_locator is None:
                    return BrowserFallbackResult(
                        available=True, success=False, status="no_link",
                        reason=f"No PDF affordance matched on {landed!r}",
                        diagnostics={"final_url": landed, "tried_selectors": len(selectors)},
                    )

                try:
                    with page.expect_download(timeout=DEFAULT_DOWNLOAD_TIMEOUT_MS) as dl_info:
                        target_locator.click(timeout=5_000)
                    download = dl_info.value
                    download.save_as(str(target_path))
                except Exception as exc:  # noqa: BLE001 — click ok but no download event
                    return BrowserFallbackResult(
                        available=True, success=False, status="challenge_timeout",
                        reason=(
                            f"Clicked PDF link but no download fired within "
                            f"{DEFAULT_DOWNLOAD_TIMEOUT_MS//1000}s (likely SSO/CAPTCHA challenge): "
                            f"{type(exc).__name__}: {exc}"
                        ),
                        diagnostics={"final_url": landed},
                    )

                if not _is_pdf(target_path):
                    return BrowserFallbackResult(
                        available=True, success=False, status="challenge_timeout",
                        reason="Downloaded payload is not a PDF (likely CAPTCHA / SSO HTML)",
                        diagnostics={"final_url": landed, "saved_bytes": target_path.stat().st_size},
                    )

                LOGGER.info("Browser fallback: saved %s (%d bytes)", target_path, target_path.stat().st_size)
                return BrowserFallbackResult(
                    available=True, success=True, saved_path=target_path,
                    status="downloaded",
                    diagnostics={"final_url": landed},
                )

            finally:
                context.close()

    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Browser fallback for %s raised %s: %s",
                       doi, type(exc).__name__, exc)
        return BrowserFallbackResult(
            available=True, success=False, status="error",
            reason=f"{type(exc).__name__}: {exc}",
        )


# --- Internals ---------------------------------------------------------------


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _doi_to_pdf_path(doi: str, output_dir: Path) -> Path:
    slug = _SLUG_RE.sub("_", doi.lower()).strip("_")
    return output_dir / f"{slug}.pdf"


def _resolve_headless(explicit: Optional[bool], family: str) -> bool:
    if explicit is not None:
        return explicit
    env = os.environ.get("BROWSER_FALLBACK_HEADLESS")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    # Empirically, ACS / Wiley SI capture works less well in headless mode.
    if family in {"acs", "wiley"}:
        return False
    # Otherwise: headed when we can see a window (Windows / macOS / Linux with DISPLAY),
    # headless on truly headless Linux boxes (e.g. CI, server SSH).
    # ``os.uname`` is POSIX-only; ``platform.system()`` is cross-platform.
    import platform
    system = platform.system()  # "Windows" | "Darwin" | "Linux" | ...
    if system in {"Windows", "Darwin"}:
        return False  # Always have a display on desktop OSes
    # Linux / other Unix: headed only when DISPLAY (X11) or WAYLAND_DISPLAY is set
    return not bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _default_profile_dir() -> Path:
    override = os.environ.get("BROWSER_FALLBACK_PROFILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "auto_paper_download" / "browser_profile"


def _is_disabled() -> bool:
    val = os.environ.get("BROWSER_FALLBACK_ENABLED", "").strip().lower()
    return val in {"0", "false", "no", "off"}


def _auth_host_fragments() -> tuple[str, ...]:
    extra = os.environ.get("BROWSER_FALLBACK_AUTH_HOSTS", "")
    extra_tuple = tuple(s.strip().lower() for s in extra.split(",") if s.strip())
    return DEFAULT_AUTH_HOST_FRAGMENTS + extra_tuple


def _looks_like_auth_redirect(url: str) -> bool:
    if not url:
        return False
    lowered = url.lower()
    for frag in _auth_host_fragments():
        if frag in lowered:
            return True
    for frag in DEFAULT_AUTH_URL_FRAGMENTS:
        if frag in lowered:
            return True
    return False


def _resolve_first_match(page, selectors: Sequence[str]):
    """Return the first Locator that resolves to a visible element, or ``None``.

    Split out from clicking so the caller can decide whether to enter a
    ``page.expect_download()`` context. Putting the selector probe inside that context
    would block until the download timeout fires when no PDF link exists at all.
    """
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if locator.count() == 0:
                continue
            locator.scroll_into_view_if_needed(timeout=2_000)
            LOGGER.debug("Browser fallback: resolved selector %r", sel)
            return locator
        except Exception as exc:  # noqa: BLE001 — best-effort; try next selector
            LOGGER.debug("Selector %r failed: %s", sel, exc)
            continue
    return None


def _click_first_match(page, selectors: Sequence[str]) -> bool:
    """Try each CSS selector; click the first one that resolves to a visible element.

    Retained for callers that don't need to drive ``expect_download()`` themselves.
    """
    locator = _resolve_first_match(page, selectors)
    if locator is None:
        return False
    try:
        locator.click(timeout=5_000)
        return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Resolved selector failed to click: %s", exc)
        return False


def _is_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False
