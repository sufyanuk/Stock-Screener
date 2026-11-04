import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.imports.options_csv import ImportError_, parse
from app.models import Recommendation, ScanResult
from app.services.engine import ScanEngine
from tests.conftest import AS_OF, FakeDataSource

# Barchart-style export: percent text, thousands separators, quoted fields, footer line
BARCHART = '''Symbol,Name,Last,Change,%Change,"IV Rank","Options Vol","1M Vol %Chg","Put Vol","Call Vol","P/C Vol",Time
IRON,"Disc Medicine Inc",70.21,+0.38,+0.54%,3.27%,"10,035","+1,639.17%",4,"10,031",0.00,11/04/26
CLPT,"Clearpoint Neuro Inc",16.81,+3.47,+26.01%,45.67%,"10,685","+937.38%","2,006","8,679",0.23,11/04/26
BAD SYMBOL,"x",1,0,0%,1%,1,1%,1,1,1,11/04/26
"Downloaded from Barchart.com as of 11-04-2026 09:41am CST"
'''

# Same columns as the user's spreadsheet "UOV" sheet: plain numbers, percents stored as fractions, many dates
SHEET = '''Symbol,Name,Latest,Change,%Change,IV Rank,Options Vol,1M Vol %Chg,Put Vol,Call Vol,P/C Vol,Date
IRON,Disc Medicine Inc,70.21,0.38,0.0054,0.0327,10035,16.3917,4,10031,0.0003987638,2026-11-04 00:00:00
DVA,Davita Healthcare Partners,210.58,1.6,0.0077,0.1885,12671,12.036,59,12612,0.0046780843640977,2026-11-04 00:00:00
IRON,Disc Medicine Inc,69.00,0.1,0.001,0.03,500,0.5,1,499,0.002,2026-11-03 00:00:00
'''


def test_parse_barchart_percent_text_and_footer():
    r = parse(BARCHART)
    assert r.percent_interpretation == "percent text"
    assert r.session_date == date(2026, 11, 4)
    iron = next(x for x in r.rows if x.symbol == "IRON")
    assert iron.vol_change_1m_pct == pytest.approx(1639.17) and iron.iv_rank == pytest.approx(3.27)
    assert iron.options_volume == 10035 and iron.call_volume == 10031 and iron.price == 70.21
    assert len(r.rows) == 2 and r.skipped and "invalid symbol" in r.skipped[0]


def test_parse_spreadsheet_fractions_and_multiple_dates():
    r = parse(SHEET)
    assert r.percent_interpretation.startswith("fractions")
    assert r.session_date == date(2026, 11, 4)
    assert {x.symbol for x in r.rows} == {"IRON", "DVA"}  # the 11-03 IRON row is ignored, not merged
    iron = next(x for x in r.rows if x.symbol == "IRON")
    assert iron.vol_change_1m_pct == pytest.approx(1639.17) and iron.iv_rank == pytest.approx(3.27)
    assert iron.pc_ratio == pytest.approx(0.0003987638)
    assert any("other than 2026-11-04" in w for w in r.warnings)
    # explicit date picks the older session instead
    assert parse(SHEET, session_override=date(2026, 11, 3)).rows[0].options_volume == 500


def test_parse_percent_mode_and_errors():
    plain = "Symbol,IV Rank,Options Vol,1M Vol %Chg,Put Vol,Call Vol\nAAA,45,1000,350,10,990\n"
    assert parse(plain, "percent").rows[0].iv_rank == 45
    r = parse(plain)  # auto -> fractions, flags the implausible value
    assert r.rows[0].iv_rank == 4500 and r.warnings
    assert parse(plain, "percent").rows[0].pc_ratio == pytest.approx(10 / 990)
    with pytest.raises(ImportError_, match="missing required column"):
        parse("Symbol,Name\nAAA,x\n")
    with pytest.raises(ImportError_):
        parse("")


def fake_csv(session="10/30/2026"):
    rows = ["Symbol,Name,Latest,IV Rank,Options Vol,1M Vol %Chg,Put Vol,Call Vol,P/C Vol,Date"]
    for t, put, price in (("GOOD", 300, 20.3), ("PCR", 3000, 20.3), ("BANK", 300, 20.3), ("PRICY", 300, 150.0)):
        rows.append(f"{t},{t} Inc,{price},62.5%,{9000 + put},830%,{put},9000,{put / 9000:.4f},{session}")
    return "\n".join(rows) + "\n"


@pytest.fixture
def client(db, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "make_datasource", lambda: FakeDataSource())
    return TestClient(main.app)


def _admin(client):
    r = client.post("/api/auth/register", json={"email": "admin@example.com", "password": "correct horse battery"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_import_api_and_scan_from_csv(client, db):
    h = _admin(client)
    body = {"filename": "uov.csv", "csv": fake_csv()}
    pre = client.post("/api/admin/options-import", headers=h, json=body | {"dry_run": True}).json()
    assert pre["dry_run"] and pre["summary"]["rows"] == 4 and client.get("/api/options-imports", headers=h).json() == []
    r = client.post("/api/admin/options-import", headers=h, json=body)
    assert r.status_code == 200 and r.json()["batch"]["session_date"] == "2026-10-30"

    run = asyncio.run(ScanEngine(db, FakeDataSource()).run(kind="close", as_of=AS_OF, live=True))
    assert run.status == "ok", run.errors
    assert run.funnel["options_source"]["type"] == "csv" and run.funnel["price_filtered_out"] == 1
    recs = db.scalars(select(Recommendation).where(Recommendation.run_id == run.id)).all()
    assert [x.ticker for x in recs] == ["GOOD"]
    good = db.scalars(select(ScanResult).where(ScanResult.run_id == run.id, ScanResult.ticker == "GOOD")).first()
    m = good.criteria["options"]["metrics"]
    assert m["volume_change_pct"] == pytest.approx(830) and m["iv_rank"] == pytest.approx(62.5)
    assert m["baseline_volume"] == pytest.approx(9300 / 9.3) and "CSV import" in m["source"]
    assert good.criteria["insider"]["outcome"] == "pass"  # insider still from the SEC (fake) source
    assert client.get("/api/health").json()["options_source"]["type"] == "csv"


def test_stale_import_never_passes_and_admin_only(client, db):
    h = _admin(client)
    client.post("/api/admin/options-import", headers=h, json={"filename": "old.csv", "csv": fake_csv("10/28/2026")})
    run = asyncio.run(ScanEngine(db, FakeDataSource()).run(kind="close", as_of=AS_OF, live=True))
    assert run.status == "partial" and any("import a current file" in e for e in run.errors)
    assert db.scalars(select(Recommendation).where(Recommendation.run_id == run.id)).all() == []
    # no import at all -> clear message, nothing recommended
    batch_id = client.get("/api/options-imports", headers=h).json()[0]["id"]
    assert client.delete(f"/api/admin/options-import/{batch_id}", headers=h).status_code == 200
    run2 = asyncio.run(ScanEngine(db, FakeDataSource()).run(kind="close", as_of=AS_OF, live=True))
    assert any("no unusual-options CSV imported" in e for e in run2.errors)
    # regular users cannot import
    u = client.post("/api/auth/register", json={"email": "u@example.com", "password": "correct horse battery"}).json()
    r = client.post("/api/admin/options-import", headers={"Authorization": f"Bearer {u['token']}"},
                    json={"filename": "x.csv", "csv": fake_csv()})
    assert r.status_code == 403
    assert client.post("/api/admin/options-import", headers=h,
                       json={"filename": "x.csv", "csv": "Symbol\nAAA\n"}).status_code == 422
