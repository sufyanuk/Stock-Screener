# Screening methodology & formulas

The authoritative text is in the code docstrings (also shown in the app under **Methodology**).
This page summarises them.

## Eligibility vs ranking

| Type | Rule | Configurable? |
|---|---|---|
| Mandatory | Shariah status = **Compliant** under the selected methodology | No |
| Mandatory | Sourced price **< $100** (missing price fails) | No (can only be lowered) |
| Hard filter (default) | Options: volume change **≥ 300%**, IV rank **> 20**, P/C **< 0.20** | Thresholds and hard/soft. Any relaxation is recorded on the scan and shown as a banner. |
| Hard filter (default) | Insider open-market purchases **> 1%** of shares outstanding | Same |
| Hard filter (default) | Within **3%** of a validated support zone, not broken down | Same |
| Ranking factor | Analyst, float, liquidity | Can be made hard |
| Universal | **Missing data = fail.** Never more than 5 picks; fewer if fewer qualify; none if none qualify. | No |

**Score** = 0.25·Options + 0.20·Insider + 0.15·Analyst + 0.20·Technical + 0.10·Float + 0.10·Liquidity
(weights are admin-configurable but must sum to 100). Ties are broken alphabetically for determinism.

## Options
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

## Shariah
See the methodology table in `backend/app/screening/shariah.py` (AAOIFI default; DJIM, MSCI, S&P and FTSE are selectable).
Missing inputs or financials older than about 15 months → **Needs Review** (excluded from the Top 5).
Purification = income × non-compliant income %.

## Position sizing / expectancy
See `backend/app/tools/position.py` and `expectancy.py`. The spreadsheet example
($3,000 × 2% ÷ $5 = 12 shares) is a unit test.
