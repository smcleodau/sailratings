"""IRC certificate pipeline: discovery download, PDF parse and DB upsert.

Covers the Nov 2026 breakages that froze ``irc_certificates`` at 2026-05-17:
signed download links expiring before use, stale constraint name in the
upsert, Single Event Rating expiry dates, wayback filenames treated as cert
numbers, reissues never re-downloaded, and certs never linked to boats.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from irc_data.parsers.certificate_pdf import (
    looks_like_certificate,
    parse_certificate_pdf,
    parse_expiry_date,
)
from irc_data.parsers.schemas import CertificateData
from irc_data.scrapers import certificate_bulk as cb

FIXTURES = Path(__file__).parent / "scrapers" / "fixtures"
SAMPLE_PDF = Path(__file__).parent / "parsers" / "fixtures" / "sample_certificate.pdf"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("31 Dec 26", date(2026, 12, 31)),
        ("31 May 2027", date(2027, 5, 31)),
        ("18 Oct 25 SINGLE EVENT RATING", date(2025, 10, 18)),
        ("19 Jun 26 RATING D'ÉVÉNEMENT UNIQUE", date(2026, 6, 19)),
        ("31 déc. 26", date(2026, 12, 31)),
        ("31 Dec 26 changes aimed at improving the fairness", date(2026, 12, 31)),
        ("31 Déc 25", date(2025, 12, 31)),
        (None, None),
        ("n/a", None),
    ],
)
def test_parse_expiry_date_variants(raw, expected):
    assert parse_expiry_date(raw) == expected


def test_looks_like_certificate():
    assert looks_like_certificate("Name: KOA\nCert No.: 14163\nHULL LH 9.96")
    assert not looks_like_certificate("IRC Congress annual report 2021\nRule changes")


def test_sample_certificate_parses_weight_with_thousands_separator():
    cert = parse_certificate_pdf(SAMPLE_PDF)
    assert cert.cert_number == "10015"
    assert cert.issue_date == date(2025, 12, 31)
    assert cert.displacement == 2800  # "2,800" kg, not 2.8
    assert cert.raw_data["is_certificate"] is True
    assert cert.raw_data["expiry_date"] == "2026-12-31"


def test_resolve_cert_number_rejects_wayback_timestamps(tmp_path):
    from irc_data.parsers.certificate_ingest import resolve_cert_number

    junk = CertificateData(cert_number=None, raw_data={"is_certificate": False})
    path = tmp_path / "20251231141539_IRC-Annual-Report.pdf"
    assert resolve_cert_number(junk, path) is None
    # A real certificate without a printed number falls back to a sane prefix.
    real = CertificateData(cert_number=None, raw_data={"is_certificate": True})
    assert resolve_cert_number(real, tmp_path / "14163_KOA_AUS52152.pdf") == "14163"
    assert resolve_cert_number(real, tmp_path / "20251231141539_49724_DAGUET.pdf") is None
    printed = CertificateData(cert_number="49724", raw_data={})
    assert resolve_cert_number(printed, path) == "49724"


# ---------------------------------------------------------------------------
# Discovery / download
# ---------------------------------------------------------------------------


def test_results_parser_uses_irc_dl_filename_from_signed_links():
    html = (FIXTURES / "irc_pdf_search_multi.html").read_text()
    results = cb._parse_results_html(html)
    names = {r["filename"] for r in results}
    assert "14163_KOA_AUS52152.pdf" in names
    assert "48182_KOA - SEC_AUS52152.pdf" in names
    koa = next(r for r in results if r["filename"] == "14163_KOA_AUS52152.pdf")
    assert koa["cert_number"] == "14163" and koa["sail_number"] == "AUS52152"
    assert "tk=" in koa["url"] and "&#038;" not in koa["url"]


def test_results_parser_still_handles_legacy_pdfdirectory_links():
    html = '<div id="pdf-results"><a href="https://ircrating.org/pdfdirectory/123_A%20B_GBR1.pdf">x</a></div>'
    [r] = cb._parse_results_html(html)
    assert r["filename"] == "123_A B_GBR1.pdf" and r["cert_number"] == "123"


def test_save_pdf_archives_superseded_copy(tmp_path):
    assert cb.save_pdf(b"%PDF-2025", "1_A_B.pdf", tmp_path) == "new"
    assert cb.save_pdf(b"%PDF-2025", "1_A_B.pdf", tmp_path) == "unchanged"
    assert cb.save_pdf(b"%PDF-2026", "1_A_B.pdf", tmp_path) == "updated"
    assert (tmp_path / "1_A_B.pdf").read_bytes() == b"%PDF-2026"
    archived = list((tmp_path / cb.SUPERSEDED_DIRNAME).glob("1_A_B__*.pdf"))
    assert len(archived) == 1 and archived[0].read_bytes() == b"%PDF-2025"


class _Resp:
    def __init__(self, status=200, text="", content=b""):
        self.status_code = status
        self.text = text
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _results_html(*files, token="t1"):
    links = "".join(
        f'<p>{f} <a href="https://ircrating.org/?irc_dl={f}&#038;tk={token}">Download</a></p>' for f in files
    )
    return f'<div id="pdf-results">{links}</div>'


class _FakeClient:
    """Tokens are valid only for the most recent search (like a short TTL)."""

    def __init__(self, listing):
        self.listing = listing  # term -> filenames
        self.calls = []
        self.searches = 0

    async def post(self, url, data, headers=None):
        self.searches += 1
        self.calls.append(("POST", data["pdf_search"]))
        files = [f for f in self.listing if data["pdf_search"] in f]
        return _Resp(text=_results_html(*files, token=f"t{self.searches}"))

    async def get(self, url, headers=None):
        self.calls.append(("GET", url))
        if not url.endswith(f"tk=t{self.searches}"):
            return _Resp(status=403)
        return _Resp(content=b"%PDF-" + url.encode())


class _NoWait:
    async def wait(self):
        return None


def test_harvester_downloads_right_after_each_search(tmp_path):
    client = _FakeClient(["14163_KOA_AUS52152.pdf", "9348_KARAKOA_PHI8088.pdf"])
    h = cb.CertificateHarvester(client, output_dir=tmp_path, limiter=_NoWait(), log=lambda *_: None)
    asyncio.run(h.harvest_term("KOA"))
    asyncio.run(h.harvest_term("KA"))
    assert (tmp_path / "14163_KOA_AUS52152.pdf").exists()
    assert (tmp_path / "9348_KARAKOA_PHI8088.pdf").exists()
    assert h.stats.new == 2 and h.stats.failed == 0
    # Each file downloaded once even though both searches list KARAKOA.
    assert sum(1 for c in client.calls if c[0] == "GET") == 2


def test_harvester_refreshes_expired_token_by_cert_number(tmp_path):
    client = _FakeClient(["14163_KOA_AUS52152.pdf"])
    h = cb.CertificateHarvester(client, output_dir=tmp_path, limiter=_NoWait(), log=lambda *_: None)
    info = {"url": "https://ircrating.org/?irc_dl=14163_KOA_AUS52152.pdf&tk=old",
            "filename": "14163_KOA_AUS52152.pdf", "cert_number": "14163"}
    assert asyncio.run(h.download(info)) == "new"
    assert h.stats.token_refreshes == 1
    assert ("POST", "14163") in client.calls


def test_harvester_skips_existing_unless_cert_due_refresh(tmp_path):
    (tmp_path / "14163_KOA_AUS52152.pdf").write_bytes(b"%PDF-old")
    (tmp_path / "9348_KARAKOA_PHI8088.pdf").write_bytes(b"%PDF-old")
    client = _FakeClient(["14163_KOA_AUS52152.pdf", "9348_KARAKOA_PHI8088.pdf"])
    h = cb.CertificateHarvester(client, output_dir=tmp_path, refresh={"14163"},
                                limiter=_NoWait(), log=lambda *_: None)
    asyncio.run(h.harvest_term("KOA"))
    assert h.stats.updated == 1 and h.stats.skipped_existing == 1
    assert (tmp_path / "14163_KOA_AUS52152.pdf").read_bytes() != b"%PDF-old"
    assert (tmp_path / "9348_KARAKOA_PHI8088.pdf").read_bytes() == b"%PDF-old"


def test_exhaustive_terms_are_two_characters():
    terms = cb.exhaustive_terms()
    assert len(terms) == 26 * 26 + 26 * 10
    assert all(len(t) == 2 for t in terms)


# ---------------------------------------------------------------------------
# DB: upsert guard, ingest, linking, refresh plan (isolated schema)
# ---------------------------------------------------------------------------


@pytest.fixture()
def pg_engine():
    from irc_data.db.models import Base, Boat, Certificate, TCCSnapshotModel

    url = os.environ.get("DATABASE_URL", "postgresql+psycopg://irc:irc@localhost:5433/irc_data")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    schema = f"test_irc_certs_{uuid.uuid4().hex[:10]}"
    admin = create_engine(url)
    try:
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
    except Exception as exc:  # noqa: BLE001
        admin.dispose()
        pytest.skip(f"PostgreSQL not available for integration test: {exc}")
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE design_classes (name_canonical text PRIMARY KEY)"
            ))
        Base.metadata.create_all(
            engine, tables=[Boat.__table__, TCCSnapshotModel.__table__, Certificate.__table__]
        )
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def _cert(cert_number, issue, lh="9.00"):
    return {"cert_number": cert_number, "issue_date": issue, "source": "ircrating.org",
            "lh": lh, "raw_data": {"name": "KOA", "sail_number": "AUS52152"}}


def test_upsert_never_replaces_newer_cert_with_older(pg_engine):
    from irc_data.db.operations import upsert_certificate_status as up

    assert up(pg_engine, None, _cert("14163", date(2025, 12, 31), "9.00"))[1] == "inserted"
    assert up(pg_engine, None, _cert("14163", date(2024, 12, 31), "8.00"))[1] == "kept_newer"
    assert up(pg_engine, None, _cert("14163", None, "7.00"))[1] == "kept_newer"
    assert up(pg_engine, None, _cert("14163", date(2026, 12, 31), "9.50"))[1] == "updated"
    with pg_engine.connect() as conn:
        row = conn.execute(text("SELECT issue_date, lh FROM irc_certificates")).one()
    assert row[0] == date(2026, 12, 31) and float(row[1]) == 9.5


def test_upsert_keeps_existing_boat_link(pg_engine):
    from irc_data.db.operations import upsert_certificate_status as up

    with pg_engine.begin() as conn:
        conn.execute(text("INSERT INTO boats (id, boat_name, sail_number, cert_number) VALUES (7, 'KOA', 'AUS52152', '14163')"))
    up(pg_engine, 7, _cert("14163", date(2025, 12, 31)))
    up(pg_engine, None, _cert("14163", date(2025, 12, 31)))
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT boat_id FROM irc_certificates")).scalar() == 7


def test_ingest_parses_links_and_skips_unchanged(pg_engine, tmp_path):
    from irc_data.parsers.certificate_ingest import ingest_certificates, iter_certificate_files

    shutil.copy(SAMPLE_PDF, tmp_path / "10015_TEST BOAT_GBR1234.pdf")
    (tmp_path / "20251231141539_report.pdf").write_bytes(b"not really a pdf")
    paths = iter_certificate_files([tmp_path])
    stats = ingest_certificates(pg_engine, paths, log=lambda *_: None)
    assert stats.inserted == 1 and stats.parse_errors == 1 and stats.db_errors == 0
    with pg_engine.connect() as conn:
        row = conn.execute(text("SELECT cert_number, boat_id, displacement_kg FROM irc_certificates")).one()
    assert row[0] == "10015" and row[1] is None and float(row[2]) == 2800

    # Boat arrives later via the TCC import -> next run links it, and the
    # unchanged PDF is not re-parsed.
    with pg_engine.begin() as conn:
        conn.execute(text("INSERT INTO boats (id, boat_name, sail_number, cert_number) VALUES (3, 'TEST BOAT', 'GBR1234', '10015')"))
    stats = ingest_certificates(pg_engine, iter_certificate_files([tmp_path]), log=lambda *_: None)
    assert stats.skipped_unchanged == 1 and stats.linked_after == 1
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT boat_id FROM irc_certificates")).scalar() == 3


def test_refresh_plan_targets_missing_expired_and_tcc_changed(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO boats (id, boat_name, sail_number, cert_number) VALUES
              (1, 'FRESH', 'GBR1', '100'), (2, 'EXPIRED', 'GBR2', '200'),
              (3, 'MISSING', 'GBR3', '300'), (4, 'CHANGED', 'GBR4', '400');
            INSERT INTO tcc_snapshots (boat_id, snapshot_date, tcc) VALUES
              (1, current_date - 30, 1.000), (1, current_date, 1.000),
              (2, current_date, 1.010), (3, current_date, 1.020),
              (4, current_date - 30, 1.030), (4, current_date, 1.040);
            INSERT INTO irc_certificates (cert_number, issue_date, scraped_at) VALUES
              ('100', current_date - 100, now() - interval '20 days'),
              ('200', current_date - 400, now() - interval '20 days'),
              ('400', current_date - 100, now() - interval '20 days');
        """))
    assert cb.refresh_cert_numbers(pg_engine) == {"200", "300", "400"}
    assert cb.missing_current_cert_numbers(pg_engine) == ["300"]
    assert cb.missing_current_cert_numbers(pg_engine, listed={"300"}) == []


# ---------------------------------------------------------------------------
# Download quota (HTTP 429 "Too many downloads from your connection")
# ---------------------------------------------------------------------------


class _Resp429(_Resp):
    def __init__(self, retry_after="600"):
        super().__init__(status=429, text="Too many downloads from your connection.")
        self.headers = {"retry-after": retry_after}


class _QuotaClient(_FakeClient):
    def __init__(self, listing, refuse_first_n):
        super().__init__(listing)
        self.refuse = refuse_first_n

    async def get(self, url, headers=None):
        if self.refuse > 0:
            self.refuse -= 1
            self.calls.append(("GET429", url))
            return _Resp429()
        self.calls.append(("GET", url))
        return _Resp(content=b"%PDF-" + url.encode())


class _Limiter:
    def __init__(self):
        self.min_delay = 2.0

    async def wait(self):
        return None


def test_harvester_honours_retry_after_and_slows_downloads(tmp_path):
    slept = []

    async def _sleep(s):
        slept.append(s)

    client = _QuotaClient(["14163_KOA_AUS52152.pdf"], refuse_first_n=1)
    dl = _Limiter()
    h = cb.CertificateHarvester(client, output_dir=tmp_path, limiter=_NoWait(), download_limiter=dl,
                                log=lambda *_: None, sleep=_sleep)
    asyncio.run(h.harvest_term("KOA"))
    assert (tmp_path / "14163_KOA_AUS52152.pdf").exists()
    assert slept and slept[0] >= 600
    assert dl.min_delay >= 15
    assert h.stats.rate_limited == 1 and h.stats.failed == 0


def test_harvester_stops_run_when_quota_never_recovers(tmp_path):
    async def _sleep(s):
        return None

    client = _QuotaClient(["14163_KOA_AUS52152.pdf"], refuse_first_n=99)
    h = cb.CertificateHarvester(client, output_dir=tmp_path, limiter=_NoWait(), download_limiter=_Limiter(),
                                log=lambda *_: None, sleep=_sleep)
    with pytest.raises(cb.DownloadQuotaExhausted):
        asyncio.run(h.harvest_term("KOA"))
    assert h.stats.rate_limited == cb.MAX_CONSECUTIVE_429 + 1


def test_discover_and_download_reports_block_instead_of_crashing(tmp_path, monkeypatch):
    async def _sleep(s):
        return None

    client = _QuotaClient(["14163_KOA_AUS52152.pdf"], refuse_first_n=99)

    class _Ctx:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(cb, "get_http_client", lambda: _Ctx())
    monkeypatch.setattr(cb, "exhaustive_terms", lambda: ["KO"])

    def _factory(c, **kw):
        return cb.CertificateHarvester(c, limiter=_NoWait(), download_limiter=_Limiter(), sleep=_sleep, **kw)

    out = asyncio.run(cb.discover_and_download(None, output_dir=tmp_path, log=lambda *_: None,
                                               harvester_factory=_factory))
    assert "blocked" in out and out["stats"]["rate_limited"] > 0
