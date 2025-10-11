from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_paper_download import downloader  # noqa: E402


def write_savedrecs(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "savedrecs.xls"
    path.write_text(content, encoding="latin-1")
    return path


def test_extract_dois_deduplicates_and_strips_control_chars(tmp_path: Path) -> None:
    sample = (
        "Some header\r\n"
        "10.1002/example\u0001\r\n"
        "Another line 10.1016/example\n"
        "Duplicate DOI 10.1002/example\n"
        "Unsupported 10.5555/example\n"
    )
    savedrecs = write_savedrecs(tmp_path, sample)
    dois = downloader.extract_dois(savedrecs)
    assert dois == ["10.1002/example", "10.1016/example", "10.5555/example"]


@pytest.mark.parametrize(
    ("doi", "expected"),
    [
        ("10.1002/abc", "Wiley"),
        ("10.1111/xyz", "Wiley"),
        ("10.1016/j.jmb.2020.01.01", "Elsevier"),
        ("10.1011/someid", "Elsevier"),
        ("10.5555/unsupported", None),
    ],
)
def test_classify_publisher(doi: str, expected: str | None) -> None:
    assert downloader.classify_publisher(doi) == expected


def test_records_from_dois_skips_unsupported_publishers() -> None:
    records = downloader.records_from_dois(
        ["10.1002/foo", "10.5555/bar", "10.1016/baz"]
    )
    publishers = [record.publisher for record in records]
    assert publishers == ["Wiley", "Elsevier"]
