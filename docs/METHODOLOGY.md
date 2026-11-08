# Screening methodology & formulas

The authoritative text is in the code docstrings (also shown in the app under **Methodology**).
This page summarises them.

## Eligibility vs ranking

**Shariah status does not filter or rank.** Each evaluated stock is still screened (with the methodology chosen on the Shariah tab, FTSE by default) and
its ticker is shown in **green** when compliant, **red** when non-compliant and **amber** when doubtful /
insufficient data; tickers not screened yet keep the normal (blue) colour.

| Type | Rule | Configurable? |
|---|---|---|
| Mandatory | Sourced price **< $100** (missing price fails) | No (can only be lowered) |
| Hard filter (default) | Options: volume change **≥ 300%**, IV rank **> 20**, P/C **< 0.20** | Thresholds and hard/soft. Any relaxation is recorded on the scan and shown as a banner. |
| Hard filter (default) | Insider open-market purchases **> 1%** of shares outstanding | Same |
| Hard filter (default) | Within **3%** of a validated support zone, not broken down | Same |
| Ranking factor | Analyst, float, liquidity | Can be made hard |
| Universal | **Missing data = fail.** Never more than 5 picks; fewer if fewer qualify; none if none qualify. | No |

**Score** = 0.25·Options + 0.20·Insider + 0.15·Analyst + 0.20·Technical + 0.10·Float + 0.10·Liquidity
(weights are admin-configurable but must sum to 100). Ties are broken alphabetically for determinism.

## Options

**Data source (default: CSV import).** Unusual-options data comes from the CSV you upload under
Admin → *Unusual options — CSV import* (Barchart "Unusual Options Volume" export, or the same columns
from a spreadsheet: Symbol, Name, Latest, Change, %Change, IV Rank, Options Vol, 1M Vol %Chg, Put Vol,
Call Vol, P/C Vol, Date). With a CSV:
* Volume change % = the file's **1M Vol %Chg** (the source's own month-over-month definition);
  baseline volume shown = Options Vol ÷ (1 + change).
* IV rank and put/call come from the file (P/C is computed as Put Vol ÷ Call Vol if the column is missing).
* Percent columns: values with a % sign are percents; plain numbers are read as fractions (0.2 = 20%)
  unless you choose otherwise when importing. The preview shows how the file was read.
* One session per import: rows dated other than the chosen session are ignored.
* An import older than the last completed trading session is **missing**, never current.
* Insider data is unaffected: it still comes from SEC EDGAR Form 4 filings.

**Alternative (Admin → Options data source = ORATS API):**
* Volume = calls + puts traded in the session, all expirations. Open interest is separate.
* Baseline (default) = mean daily volume of the 20 sessions **ending 21 sessions before** the
  evaluation session (equivalent one-session comparison, one month earlier). The alternative is
  the single session 21 sessions earlier. A baseline under 100 contracts counts as insufficient.
* Change % = (current ÷ baseline − 1) × 100.
* IV rank = (IV30ₜ − min) ÷ (max − min) × 100 over the last 252 sessions (≥ 200 observations).
  If there isn't enough stored history, the provider's 1-year rank is used and labelled.
* Put/call = put volume ÷ call volume for the session.
* Sub-score: 50% volume change (150%→0 … 1500%→100), 25% IV rank, 25% P/C (1.0→0 … 0→100).

## Insider (SEC Form 4)
* Qualifying = non-derivative, code **P**, acquired, price > 0. Every other code is excluded and listed with the reason.
* Duplicates removed: re-ingestion (accession + index + owner); 4/A supersedes the original;
  joint/indirect filings of the same issuer/date/size/price collapse into **one** economic purchase.
* % = shares ÷ latest `dei:EntityCommonStockSharesOutstanding` filed on or before the date (multiple share classes are summed).
* Basis: aggregate of unique purchases (default) or the largest single purchase. Both are always displayed.
* Sub-score: 70% size (0→5% of outstanding), 15% senior-executive buyer, 15% number of distinct insiders.

## Analyst
* Consensus = weighted mean on a 1–5 scale. Upside = avg target ÷ price − 1.
* Up/downgrades over 30 and 90 days. Accuracy weighting is applied **only** when a sourced success rate exists.
* Sub-score: 50% consensus (2.5→0, 5→100), 30% upside (0→50%), 20% net 90-day upgrades.

## Technical
* Indicators: SMA 20/50/100/200, Wilder RSI(14), MACD(12,26,9), Bollinger(20,2), ATR(14),
  20-day VWAP, relative volume, 52-week Fibonacci levels, a 120-session volume profile, and daily/weekly/monthly trend.
* Support zones: daily (±5 bars) and weekly (±2 bars) fractal lows, clustered within 1.5%;
  **validated** with ≥ 2 touches. Confluence with SMAs, high-volume nodes and Fibonacci levels is listed.
* Distance = (price − zone top) ÷ zone top. Classes: strong ≤ 1%, near ≤ 3%, approaching ≤ 5%, extended > 5%.
* Breakdown = close more than 2% below a validated zone that price was above within 20 sessions, with no reclaim.
* Plan: entry = price (range zone-top → price); stop = zone low − 0.5×ATR; T1/T2 = next
  resistance zones (or the 52-week high). Targets are never invented: if none are found, the field is empty.

## Float & liquidity
* Float ratio = float ÷ outstanding × 100. Very low < 10%, low < 20%, moderate ≤ 40%, high > 40%.
* Flags: avg dollar volume < $1M, spread > 2%, recent halt, or very low float combined with a > 25% one-day move.

## Shariah (internal engine)
Code: `backend/app/screening/shariah_engine.py` (rules and classification) and `backend/app/services/shariah.py`
(SEC data). No paid Shariah classification API is used. An analytical aid, not a fatwa.

**Results.** Each stock gets one of: HALAL / SHARIAH COMPLIANT, NON-COMPLIANT, or DOUBTFUL / INSUFFICIENT DATA.
A missing figure, missing industry code, stale balance sheet (> 456 days) or a business that needs review gives
DOUBTFUL, never HALAL. Any failed test or prohibited primary business gives NON-COMPLIANT.

**Methodologies are separate.** AAOIFI Std 21, DJIM, FTSE and S&P each have their own business exclusions,
financial tests (numerator, denominator, threshold, < or ≤) and source. Nothing is shared between them.

* **FTSE** is built in from the *FTSE Yasaar Global Equity Shariah Index Series Ground Rules v4.6 (Feb 2026)*,
  rules 4.3.1–4.3.2: debt < 33.333% of total assets; cash and interest-bearing items < 33.333% of total assets;
  accounts receivable and cash < 50% of total assets; total interest and non-compliant activities income ≤ 5% of
  total revenue. Excluded sectors: conventional finance, alcohol, pork / non-halal food, entertainment (casinos,
  gambling, cinema, music, pornography, hotels), tobacco, weapons/arms/defence (5% revenue tolerance).
* **AAOIFI, DJIM, S&P** ship with **no thresholds**. Their official documents could not be retrieved
  automatically (S&P DJI blocks automated downloads; AAOIFI's site refused the request), and S&P/DJIM changed
  their rules in 2023 and August 2026, so values were not assumed. Enter them on the Shariah tab →
  *Methodology rules* from the current official document (the document name is required and stored with an
  audit trail). Until then these methodologies mark every stock DOUBTFUL except businesses that fail the
  business screen.

**Business screen.** Uses the company's SEC industry code (SIC) and name. Prohibited primary SIC codes
(e.g. 6021/6022 banks, 6311/6331 insurers, 2082/2084/2085 alcohol, 2111 tobacco, 3482/3484 arms) → NON-COMPLIANT.
Industries where the activity is often present (hotels, restaurants, meat packing, amusement/casinos, REITs,
brokers, …) and name keywords (casino, brewery, bank, pork, adult entertainment, …) → DOUBTFUL until an
administrator records a business review (with the share of revenue from prohibited activities).

**Financial figures (SEC XBRL, frames API).** All balance-sheet lines are taken at the company's latest
balance-sheet date (from the same filing date, never mixed); income lines from the latest fiscal year.
* Interest-bearing debt = long-term debt incl. current portion (`LongTermDebt`, or non-current + current) +
  short-term borrowings / commercial paper / notes payable + finance-lease liabilities. Operating leases excluded.
* Cash + interest-bearing investments = cash & equivalents + short-term investments / current marketable or
  debt securities + non-current marketable / debt securities (conservative: all cash treated as interest-bearing).
* Accounts receivable = `AccountsReceivableNetCurrent` (or `ReceivablesNetCurrent`).
* Non-permissible income = interest income (+ prohibited revenue from a business review). If only "interest and
  other income" or "interest and dividend income" is reported, that larger figure is used (noted).
* Market cap (only for tests that use it) = shares outstanding on the same balance sheet × latest price;
  12/24/36-month averages = average of consecutive month-end closes × those shares (requires the full window).

Refreshed weekly by the daily scan; the Shariah tab continues any unfinished load.

## Position sizing / expectancy
See `backend/app/tools/position.py` and `expectancy.py`. The spreadsheet example
($3,000 × 2% ÷ $5 = 12 shares) is a unit test.
