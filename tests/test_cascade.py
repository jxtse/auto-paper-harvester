"""Download order: open access first, then the publisher TDM API (if configured)."""

from __future__ import annotations

import pytest

from auto_paper_download import clients
from auto_paper_download.clients import ArticleRecord, DownloadError
from auto_paper_download.ratelimit import Ledger


class Source:
    """Fake client that records calls and either writes a PDF or fails."""

    def __init__(self, name, calls, ok=False, body=b"%PDF-1.7 test"):
        self.name, self.calls, self.ok, self.body = name, calls, ok, body

    def download_pdf(self, *, doi=None, destination, overwrite=False, pii=None):
        self.calls.append(self.name)
        if not self.ok:
            raise DownloadError(f"{self.name} has no copy")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.body)
        return destination


@pytest.fixture(autouse=True)
def no_supplements(monkeypatch):
    monkeypatch.setattr(clients, "download_supplements_for_doi", lambda **kw: iter(()))


def run(records, tmp_path, **kw):
    metrics = {}
    out = list(clients.batched_download(records=records, output_root=tmp_path / "out",
                                        raise_on_error=False, metrics=metrics, **kw))
    return out, metrics


def test_oa_copy_wins_before_tdm_api(tmp_path):
    calls = []
    rec = ArticleRecord(title="t", doi="10.1016/j.a.1", publisher="Elsevier")
    out, m = run([rec], tmp_path,
                 openalex_client=Source("openalex", calls, ok=True),
                 elsevier_client=Source("elsevier", calls, ok=True))
    assert calls == ["openalex"]
    assert out[0].read_bytes().startswith(b"%PDF") and m["Elsevier"]["succeeded"] == 1


def test_falls_through_oa_to_tdm(tmp_path):
    calls = []
    rec = ArticleRecord(title="t", doi="10.1002/x.1", publisher="Wiley")
    out, m = run([rec], tmp_path,
                 openalex_client=Source("openalex", calls),
                 unpaywall_client=Source("unpaywall", calls),
                 crossref_client=Source("crossref", calls, ok=True),
                 wiley_client=Source("wiley", calls, ok=True))
    # Crossref's TDM link is skipped for TDM publishers; the Wiley API is called instead.
    assert calls == ["openalex", "unpaywall", "wiley"]
    assert len(out) == 1 and m["Wiley"]["failed_dois"] == []


def test_no_tdm_key_skips_step_and_reports_for_browser(tmp_path):
    calls = []
    rec = ArticleRecord(title="t", doi="10.1016/j.a.2", publisher="Elsevier")
    out, m = run([rec], tmp_path, openalex_client=Source("openalex", calls))
    assert calls == ["openalex"] and out == []
    assert [e["doi"] for e in m["Elsevier"]["failed_dois"]] == ["10.1016/j.a.2"]


def test_other_publishers_use_crossref_link_after_oa(tmp_path):
    calls = []
    rec = ArticleRecord(title="t", doi="10.1021/acs.1", publisher="Crossref")
    out, _ = run([rec], tmp_path,
                 openalex_client=Source("openalex", calls),
                 unpaywall_client=Source("unpaywall", calls),
                 crossref_client=Source("crossref", calls, ok=True))
    assert calls == ["openalex", "unpaywall", "crossref"] and len(out) == 1


def test_html_saved_as_pdf_is_rejected_and_next_source_tried(tmp_path):
    calls = []
    rec = ArticleRecord(title="t", doi="10.1002/x.2", publisher="Wiley")
    out, _ = run([rec], tmp_path,
                 openalex_client=Source("openalex", calls, ok=True, body=b"<html>login</html>"),
                 wiley_client=Source("wiley", calls, ok=True))
    assert calls == ["openalex", "wiley"]
    assert out[0].read_bytes().startswith(b"%PDF")


def test_ledger_charges_the_channel_that_delivered(tmp_path, monkeypatch):
    import auto_paper_download.ratelimit as rl

    monkeypatch.setattr(rl.time, "sleep", lambda s: None)
    led = Ledger(tmp_path / "l.sqlite")
    calls = []
    rec = ArticleRecord(title="t", doi="10.1016/j.a.3", publisher="Elsevier")
    run([rec], tmp_path, openalex_client=Source("openalex", calls),
        elsevier_client=Source("elsevier", calls, ok=True), ledger=led)
    rows = led.db.execute("select channel, status from attempts order by rowid").fetchall()
    assert rows == [("oa", "fail"), ("elsevier", "ok")]
    assert not led.db.execute("select 1 from attempts where status='pending'").fetchall()


def test_existing_pdf_skips_all_sources(tmp_path):
    calls = []
    d = tmp_path / "out" / "10.1002_x.3"
    d.mkdir(parents=True)
    (d / "10.1002_x.3.pdf").write_bytes(b"%PDF-1.7")
    rec = ArticleRecord(title="t", doi="10.1002/x.3", publisher="Wiley")
    out, _ = run([rec], tmp_path, openalex_client=Source("openalex", calls))
    assert calls == [] and len(out) == 1
