"""OpenInsider screener parsing (sample markup in the site's table layout)."""
import asyncio
from datetime import date

from app.providers import openinsider as oi
from app.screening.insider import dedupe_purchases

HTML = """<html><body><table class="tinytable">
<thead><tr><th>X</th><th>Filing&nbsp;Date</th><th>Trade&nbsp;Date</th><th>Ticker</th><th>Company&nbsp;Name</th>
<th>Insider&nbsp;Name</th><th>Title</th><th>Trade&nbsp;Type</th><th>Price</th><th>Qty</th><th>Owned</th><th>&Delta;Own</th>
<th>Value</th><th>1d</th><th>1w</th><th>1m</th><th>6m</th></tr></thead>
<tbody>
<tr><td>M</td><td><a href="http://www.sec.gov/Archives/edgar/data/1234567/000123456726000123/xslF345X05/form4.xml">2026-11-04 16:31:05</a></td>
<td>2026-11-03</td><td><a href="/ABCD">ABCD</a></td><td>Abcd Corp</td><td><a href="/insider/Doe-Jane/1789012">Doe Jane</a></td>
<td>CEO, Dir</td><td>P - Purchase</td><td>$12.50</td><td>+10,000</td><td>110,000</td><td>+10%</td><td>+$125,000</td><td></td><td></td><td></td><td></td></tr>
<tr><td>A</td><td><a href="http://www.sec.gov/Archives/edgar/data/7654321/000765432126000045/form4.xml">2026-11-02 09:00:00</a></td>
<td>2026-10-30</td><td><a href="/WXYZ">WXYZ</a></td><td>Wxyz Inc</td><td><a href="/insider/Big-Fund-LP/1555555">Big Fund LP</a></td>
<td>10%</td><td>P - Purchase</td><td>$3.10</td><td>+500,000</td><td>5,500,000</td><td>New</td><td>+$1,550,000</td><td></td><td></td><td></td><td></td></tr>
</tbody></table></body></html>"""


def test_parse_screener_and_map_rows():
    rows = oi.parse_screener(HTML)
    assert len(rows) == 2 and rows[0]["ticker"] == "ABCD" and rows[0]["qty"] == 10000 and rows[0]["price"] == 12.5
    t = oi.to_txn(rows[0])
    assert t.accession == "0001234567-26-000123" and t.owner_cik == "0001789012" and t.code == "P"
    assert (t.filing_date, t.transaction_date) == (date(2026, 11, 4), date(2026, 11, 3))
    assert t.is_director and t.is_officer and not t.is_ten_pct_owner and t.shares_owned_after == 110000
    t2 = oi.to_txn(rows[1])
    assert t2.is_ten_pct_owner and not t2.is_officer and t2.is_amendment
    assert oi.to_txn(rows[0]).txn_index == t.txn_index  # stable across re-fetches
    purchases, excluded = dedupe_purchases([t, t2])
    assert len(purchases) == 2 and not excluded


def test_openinsider_ingest_into_scan(monkeypatch, db):
    from app.models import InsiderTransaction
    from app.services.engine import ScanEngine
    from tests.conftest import FakeDataSource, prov

    class DS(FakeDataSource):
        openinsider = True
        calls: list[int] = []

        async def insider_recent(self, filed_days):
            DS.calls.append(filed_days)
            return [oi.to_txn(r) for r in oi.parse_screener(HTML)], prov("openinsider")

    eng = ScanEngine(db, DS())
    asyncio.run(eng.ingest_insiders([date(2026, 11, 4)]))
    asyncio.run(eng.ingest_insiders([date(2026, 11, 4)]))  # re-run: no duplicates
    assert db.query(InsiderTransaction).count() == 2
    assert DS.calls == [730, 730]


def test_requests_first_100_results(monkeypatch):
    p = oi.OpenInsider()
    urls = []

    async def fake_get_text(url):
        urls.append(url)
        return HTML

    monkeypatch.setattr(p, "_fetch", fake_get_text)
    rows = asyncio.run(p.purchases())
    assert len(urls) == 1 and "cnt=100&page=1" in urls[0] and "fd=730" in urls[0] and "xp=1" in urls[0]
    assert len(rows) == 2
