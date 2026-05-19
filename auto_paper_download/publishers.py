"""
Publisher routing and classification.

Maps DOI prefixes (and journal-name fragments as fallback) to publisher families.
Each family is annotated with:
  - ``family``        : canonical id used by the rest of the codebase
  - ``support``       : current support tier (full / partial / oa_only / browser_only / unsupported)
  - ``handler``       : which client/strategy to dispatch to ("wiley", "elsevier",
                       "springer", "crossref", "openalex", "browser")
  - ``notes``         : short description for ``docs/SUPPORTED_PUBLISHERS.md``

The previous codebase only recognised four publishers (Wiley / Elsevier / Springer /
fallback Crossref). This expanded map mirrors the publisher coverage of
`ltczding-gif/ref-downloader` so the same DOI set is routable through API + browser
strategies.

Note: classification only decides routing. Whether a download actually succeeds still
depends on (a) API credentials, (b) OA availability, and (c) — for ``browser_only``
publishers — whether the user opted into Playwright fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class PublisherInfo:
    """Routing + capability metadata for a single publisher family."""

    family: str
    handler: str  # "wiley" | "elsevier" | "springer" | "crossref" | "openalex" | "browser"
    support: str  # "full" | "partial" | "oa_only" | "browser_only" | "unsupported"
    display_name: str
    notes: str = ""


# Primary signal: DOI prefix → publisher family.
# Sorted roughly by frequency in materials/chemistry/physics corpora.
PUBLISHER_MAP: dict[str, PublisherInfo] = {
    # ------- API-backed (have full TDM / OA APIs) -------
    "10.1002": PublisherInfo("wiley", "wiley", "full", "Wiley",
                             "Wiley TDM API (requires WILEY_TDM_TOKEN)"),
    "10.1111": PublisherInfo("wiley", "wiley", "full", "Wiley",
                             "Wiley TDM API (requires WILEY_TDM_TOKEN)"),
    "10.1016": PublisherInfo("elsevier", "elsevier", "full", "Elsevier",
                             "Elsevier TDM API (requires ELSEVIER_API_KEY)"),
    "10.1011": PublisherInfo("elsevier", "elsevier", "full", "Elsevier",
                             "Rare Elsevier prefix (legacy)"),
    "10.1007": PublisherInfo("springer", "springer", "oa_only", "Springer Nature",
                             "Springer Open Access API; paywalled content not served"),
    "10.1186": PublisherInfo("springer", "springer", "oa_only", "Springer (BMC)",
                             "BioMed Central via Springer OA API"),
    "10.1038": PublisherInfo("nature", "springer", "oa_only", "Nature Portfolio",
                             "Nature Portfolio — OA via Springer/Unpaywall only"),
    "10.1147": PublisherInfo("springer", "springer", "oa_only", "Springer (IBM J Res Dev)",
                             "IBM Journal of R&D, now Springer-hosted"),

    # ------- OA + browser fallback only -------
    "10.1021": PublisherInfo("acs", "browser", "browser_only", "ACS",
                             "No TDM API; needs institutional browser session"),
    "10.1039": PublisherInfo("rsc", "browser", "browser_only", "RSC",
                             "RSC OA when available; browser fallback otherwise"),
    "10.1126": PublisherInfo("science", "browser", "browser_only", "AAAS / Science",
                             "Science family — no public TDM; OA + browser only"),
    "10.1073": PublisherInfo("pnas", "openalex", "partial", "PNAS",
                             "PNAS often OA after 6 months; OpenAlex/Unpaywall first"),
    "10.1149": PublisherInfo("ecs", "browser", "browser_only", "ECS",
                             "Electrochemical Society — browser session"),
    "10.1088": PublisherInfo("iop", "browser", "browser_only", "IOP",
                             "IOP Publishing — needs SSO"),
    "10.1143": PublisherInfo("iop", "browser", "browser_only", "IOP (JJAP)",
                             "Japanese Journal of Applied Physics, now IOP-hosted"),
    "10.1063": PublisherInfo("aip", "browser", "browser_only", "AIP",
                             "AIP loading-page pattern (handled by browser strategy)"),
    "10.1116": PublisherInfo("avs", "browser", "browser_only", "AVS",
                             "AVS, listed before journal-name lookup to avoid 'science' collision"),
    "10.1109": PublisherInfo("ieee", "browser", "browser_only", "IEEE",
                             "IEEE Xplore — institutional session"),
    "10.1103": PublisherInfo("aps", "browser", "browser_only", "APS",
                             "American Physical Society — needs SSO"),
    "10.1146": PublisherInfo("annualreviews", "browser", "browser_only", "Annual Reviews",
                             "Annual Reviews — browser session"),
    "10.1080": PublisherInfo("tandfonline", "browser", "browser_only", "Taylor & Francis",
                             "Taylor & Francis — browser session"),
    "10.1364": PublisherInfo("osa", "browser", "browser_only", "Optica (OSA)",
                             "Optica Publishing Group — partial OA"),
    "10.3938": PublisherInfo("kps", "browser", "browser_only", "Korean Physical Society",
                             "KPS — limited coverage"),
    "10.3762": PublisherInfo("beilstein", "openalex", "partial", "Beilstein",
                             "Beilstein journals are OA; usually downloadable via Unpaywall"),
}


# Secondary signal: journal-name fragments → publisher family.
# Only consulted when the DOI prefix is unknown.
JOURNAL_PUBLISHER_HINTS: dict[str, str] = {
    "nature": "nature",
    "nat ": "nature",
    "science": "science",
    "sci adv": "science",
    "sci. adv": "science",
    "acs ": "acs",
    "j. am. chem. soc": "acs",
    "nano lett": "acs",
    "j. phys. chem": "acs",
    "angew": "wiley",
    "adv. mater": "wiley",
    "adv mater": "wiley",
    "chemsuschem": "wiley",
    "pnas": "pnas",
    "proc. natl. acad": "pnas",
    "electrochem": "ecs",
    "j. membr": "elsevier",
    "j. power sources": "elsevier",
    "matter": "elsevier",
    "iop": "iop",
    "beilstein": "beilstein",
}


def classify_publisher(doi: str, journal: str = "") -> Optional[PublisherInfo]:
    """
    Resolve a DOI (and optional journal name) to publisher routing metadata.

    Returns ``None`` only when the DOI prefix is malformed. For unknown publishers the
    return value is a ``PublisherInfo`` with ``support='unsupported'`` and
    ``handler='crossref'`` (so generic Crossref/OpenAlex/Unpaywall fallback still runs).
    """
    if not doi:
        return None

    lowered = doi.lower()
    prefix = lowered.split("/", 1)[0] if "/" in lowered else lowered

    info = PUBLISHER_MAP.get(prefix)
    if info is not None:
        return info

    # Journal-name fallback
    if journal:
        jl = journal.lower()
        for frag, family in JOURNAL_PUBLISHER_HINTS.items():
            if frag in jl:
                # Synthesise a PublisherInfo at runtime
                # (look up an existing entry sharing this family for support tier)
                for known in PUBLISHER_MAP.values():
                    if known.family == family:
                        return known
                break

    # Truly unknown — leave it to Crossref/OpenAlex/Unpaywall generic fallback
    return PublisherInfo(
        family="unknown",
        handler="crossref",
        support="unsupported",
        display_name=f"Unknown ({prefix})",
        notes="Unrecognised DOI prefix; falling back to Crossref/OpenAlex/Unpaywall",
    )


def family_to_legacy_publisher(family: str) -> str:
    """
    Map a publisher family back to the legacy publisher label used by ``ArticleRecord``
    (``"Wiley"`` / ``"Elsevier"`` / ``"Springer"`` / ``"Crossref"``).

    The downstream client dispatcher in ``downloader.py`` still keys on these legacy
    labels; this shim lets us expand classification without breaking the existing flow.
    """
    if family in ("wiley",):
        return "Wiley"
    if family in ("elsevier",):
        return "Elsevier"
    if family in ("springer", "nature"):
        return "Springer"
    # Everything else (acs / rsc / aip / etc.) goes through generic Crossref+OA fallback
    # until/unless a browser fallback is enabled.
    return "Crossref"


def needs_browser_fallback(family: str) -> bool:
    """True iff this publisher can only be reliably downloaded via institutional browser."""
    for info in PUBLISHER_MAP.values():
        if info.family == family:
            return info.handler == "browser"
    return False
