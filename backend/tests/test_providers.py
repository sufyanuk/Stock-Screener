"""Parser and HTTP-resilience tests. The Form 4 fixture follows the SEC ownershipDocument
X0508 schema structure (synthetic values); live parsing must be re-verified with real filings."""
import asyncio
from datetime import date

import httpx
import pytest
import respx

from app.providers.http import HttpProvider, ProviderError
from app.providers.sec_edgar import fundamentals_from_xbrl, parse_form4, parse_form_index, shares_outstanding
from app.screening.insider import InsiderInput, evaluate_insider
from app.config import InsiderCriteria

FORM4 = """<SEC-DOCUMENT>0001234567-26-000001.txt
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0508</schemaVersion>
  <documentType>4</documentType>
  <periodOfReport>2026-10-28</periodOfReport>
  <issuer><issuerCik>0000012345</issuerCik><issuerName>Example Corp</issuerName><issuerTradingSymbol>exmp</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0000099999</rptOwnerCik><rptOwnerName>Smith John</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-10-28</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>150000</value></transactionShares>
        <transactionPricePerShare><value>12.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>400000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-10-28</value></transactionDate>
      <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>2000</value></transactionShares>
        <transactionPricePerShare><value>12.40</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>398000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <derivativeTable>
    <derivativeTransaction>
      <securityTitle><value>Stock Option (right to buy)</value></securityTitle>
      <transactionDate><value>2026-10-28</value></transactionDate>
      <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>50000</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>
</XML>
</SEC-DOCUMENT>"""


def test_parse_form4_and_qualify():
    rows = parse_form4(FORM4, "0001234567-26-000001", date(2026, 10, 30))
    assert len(rows) == 3
    p = rows[0]
    assert (p.ticker, p.code, p.shares, p.price, p.shares_owned_after) == ("EXMP", "P", 150000, 12.5, 400000)
    assert p.is_director and p.is_officer and p.is_senior and p.issuer_cik == "0000012345"
    assert rows[2].is_derivative
    r = evaluate_insider(InsiderInput("EXMP", date(2026, 11, 1), rows, 10_000_000, None), InsiderCriteria())
    assert r.metrics["unique_purchase_count"] == 1 and r.metrics["aggregate_pct"] == pytest.approx(1.5)
    assert r.metrics["excluded_count"] == 2
    assert r.metrics["purchases"][0]["owned_before"] == 250000


def test_parse_form_index_dedupes_accessions():
    idx = """Description: Daily Index of EDGAR Dissemination Feed by Form Type
Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
4           Example Corp                                                  12345       20261030    edgar/data/12345/0001234567-26-000001.txt
4           Smith John                                                    99999       20261030    edgar/data/99999/0001234567-26-000001.txt
4/A         Other Co                                                      55555       20261030    edgar/data/55555/0005555555-26-000009.txt
10-Q        Other Co                                                      55555       20261030    edgar/data/55555/0005555555-26-000010.txt
"""
    rows = parse_form_index(idx, {"4", "4/A"})
    assert [r["accession"] for r in rows] == ["0001234567-26-000001", "0005555555-26-000009"]
    assert rows[1]["form"] == "4/A" and rows[0]["filed"] == date(2026, 10, 30)


CF = {"facts": {
    "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2026-07-25", "val": 90_000_000, "filed": "2026-08-01", "accn": "a2", "form": "10-Q"},
        {"end": "2026-07-25", "val": 10_000_000, "filed": "2026-08-01", "accn": "a2", "form": "10-Q"},
        {"end": "2026-10-25", "val": 120_000_000, "filed": "2026-11-02", "accn": "a3", "form": "10-Q"}]}}},
    "us-gaap": {
        "Assets": {"units": {"USD": [{"end": "2026-06-30", "val": 1000, "filed": "2026-08-01", "accn": "a2", "form": "10-Q"}]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [{"end": "2026-06-30", "val": 100, "filed": "2026-08-01", "accn": "a2"}]}},
        "LongTermDebtCurrent": {"units": {"USD": [{"end": "2026-06-30", "val": 20, "filed": "2026-08-01", "accn": "a2"}]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [{"end": "2026-06-30", "val": 50, "filed": "2026-08-01"}]}},
        "AccountsReceivableNetCurrent": {"units": {"USD": [{"end": "2026-06-30", "val": 30, "filed": "2026-08-01"}]}},
        "Revenues": {"units": {"USD": [
            {"start": "2025-01-01", "end": "2025-12-31", "val": 400, "filed": "2026-02-20", "form": "10-K"},
            {"start": "2026-04-01", "end": "2026-06-30", "val": 120, "filed": "2026-08-01", "form": "10-Q"}]}},
        "InvestmentIncomeInterestAndDividend": {"units": {"USD": [
            {"start": "2025-01-01", "end": "2025-12-31", "val": 4, "filed": "2026-02-20"}]}},
    }}}


def test_xbrl_point_in_time_extraction():
    so, so_date = shares_outstanding(CF, date(2026, 10, 1))
    assert so == 100_000_000 and so_date == date(2026, 7, 25)  # multi-class summed; later filing not yet visible
    assert shares_outstanding(CF, date(2026, 11, 5))[0] == 120_000_000
    f = fundamentals_from_xbrl(CF, {"name": "Example Corp", "sic": "3571"}, "EXMP", date(2026, 10, 1), 2000.0)
    assert f.debt == 120 and f.cash_and_securities == 50 and f.receivables == 30 and f.total_assets == 1000
    assert f.revenue == 400  # annual, quarterly excluded
    assert f.interest_income == 4 and f.interest_income_includes_dividends
    assert f.filed == date(2026, 8, 1)
    # before the 10-Q was filed, the balance sheet is unavailable -> Needs Review downstream
    assert fundamentals_from_xbrl(CF, {}, "EXMP", date(2026, 7, 1), 2000.0).total_assets is None


@respx.mock
def test_http_retries_then_succeeds_and_redacts_key():
    route = respx.get("https://example.test/x").mock(side_effect=[
        httpx.Response(503), httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"ok": 1})])
    p = HttpProvider("t", "https://example.test", requests_per_second=100, max_retries=3)
    import app.providers.http as h

    orig = h.asyncio.sleep
    h.asyncio.sleep = lambda *_: orig(0)
    try:
        assert asyncio.run(p.get_json("/x", {"apiKey": "SECRET"}, cache_ttl=0)) == {"ok": 1}
        assert route.call_count == 3
        respx.get("https://example.test/y").mock(return_value=httpx.Response(400))
        with pytest.raises(ProviderError) as e:
            asyncio.run(p.get_json("/y", {"apiKey": "SECRET"}, cache_ttl=0))
        assert "SECRET" not in str(e.value)
        respx.get("https://example.test/z").mock(return_value=httpx.Response(401))
        with pytest.raises(ProviderError):
            asyncio.run(p.get_json("/z", cache_ttl=0))
    finally:
        h.asyncio.sleep = orig


@respx.mock
def test_http_cache():
    route = respx.get("https://example.test/c").mock(return_value=httpx.Response(200, json=[1]))
    p = HttpProvider("t", "https://example.test", requests_per_second=100)

    async def go():
        await p.get_json("/c")
        await p.get_json("/c")

    asyncio.run(go())
    assert route.call_count == 1
