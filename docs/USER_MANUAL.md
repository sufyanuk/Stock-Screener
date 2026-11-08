# User manual

**Scans** — the first time the app is opened on a new day it runs a scan automatically (insider buying,
earnings calendar, analyst and SentiSense ratings, bounce prices, outstanding shares); a banner shows
progress. **Run scan now** is on the Dashboard and Unusual Options tabs for an extra scan (e.g. after
importing today's options CSV). Every table column can be sorted by clicking its heading.

**Dashboard** (opens first) — near-qualified stocks failing one or two rules (with reasons), and the top 5
from each tab: unusual options, insider buying (by % of outstanding
shares), upcoming earnings, seasonality for this month, 52-week-low bounces and 50-EMA bounces.
"View all →" opens the full tab.

**Unusual Options** — import the day's unusual-options CSV at the top of this tab
(choose the file, click **Preview** to check how it was read, e.g. +1,639% and IV rank 3.3, then
**Import**; the box shows a one-line summary; import a new file each trading day). Below it, **Unusual-options criteria** sets the
thresholds (1M volume change, IV rank, put/call, data source). The table lists only the stocks in the latest
file that meet the criteria (and price < $100), sorted by 1-month options volume change, with price,
calls/puts, put/call and IV rank.

**Insider Buying** — open-market purchases (SEC Form 4) filed in the last 90 days, shown only where the
shares bought are more than 1% of the company's shares outstanding. Columns include Outstanding shares
and % of outstanding (shares bought ÷ shares outstanding). Share counts come from FMP (Finnhub /
Alpha Vantage as fallback) and are looked up automatically, cached for 30 days. **Insider-buying
criteria** sets the % of shares outstanding, look-back and basis used for the Top 5.

**Watchlist** — your custom watchlist: add any tickers (with an optional note); each shows its latest
daily open, high, low, close, change and volume. Pick a stock in "Select stock" (or click its row) for the
last 15 sessions. Prices come from your price APIs.

**Ratings** — two sub-tabs for the stocks the app follows (your watchlist, the latest options CSV, insider
purchases of the last 90 days, and earnings this week):
- **Analyst ratings — Strong Buy**: only stocks with at least one analyst rating Strong Buy (choose a higher
  minimum), with Buy / Hold / Sell / Strong Sell counts, total analysts and % Strong Buy (Finnhub; cached a day).
- **SentiSense rating**: A–F letter, 0–100 score, rank, risk points and favourable-analyst share; filter by
  letter. Loaded with SentiSense's screener (all rated stocks in one request) or per stock if the screener is not
  on your plan. A research score, not a buy/sell recommendation.

**Bounces** — every listed US common stock, from a year of daily prices (Alpaca's multi-stock daily bars,
consolidated SIP feed, split/dividend adjusted; 100 stocks per request, so the first load takes about two
minutes). The tab loads automatically when prices are missing or behind the latest session; each scan adds
the newest day, and a stock with a split-sized jump has its history reloaded. **Rebuild** reloads everything.
- **Bouncing from 52-week low**: price below $100; close within 5% of the 52-week low and at least 2% above
  it; volume more than 1.5× the 50-day average volume; optional RSI(14) 30–50.
- **Bouncing from 50 EMA**: price below $100; the day's low came within 1% of the 50-day EMA; the close is
  above the EMA; volume at least 1.5× the 20-day average; optional RSI(14) 40–60.
All thresholds can be changed on the tab. Latest completed session only.

**Seasonality** — based on your own price-history CSV (daily, weekly or monthly OHLCV). On the
Seasonality tab choose the file (one file with a Symbol/Ticker column for all stocks, or one file per
stock named by ticker, e.g. AAPL.csv), check the preview (stocks, date range, close column), then
**Import**. The file is read in your browser and only each stock's month-end close is uploaded, so
large files are fine. "Replace all existing price history" starts fresh; untick it to add or update
only the stocks in the file. "Adj Close" is used when present; use split-adjusted prices. Pick a month
to rank stocks by average return in that month (or % of years positive), with median, best/worst
year, years of data, latest price and a 12-month heatmap. Filter by price range, minimum years and
ticker prefix. "Top 5 for each month" shows the leaders for every month. A month the file only
partly covers is left out, and moves beyond ±200% in a month are ignored as data errors. Past
seasonality does not guarantee future returns.

**Stock page** — why the stock was or wasn't selected (pass/fail/missing per criterion, in plain
English); a candlestick chart with SMAs, support/resistance, entry, stop and targets; options
detail and largest contracts; insider purchases with joint-filing de-duplication; analyst
consensus and changes; float and liquidity; news; SEC
filings; and a data-provenance table. **Size this trade** opens the position calculator pre-filled.

**Calculators** — position size (account balance, risk %, entry, stop; optional fractional shares — a stop above the entry is a short) and
expectancy (R-multiples, profit factor, break-even win rate, Monte-Carlo risk of ruin and drawdown,
scenario grid).

**Data APIs** — the footer shows which data APIs are connected (● = key set). Stock prices come from
Alpaca (Finnhub fallback), price history from Tiingo (Alpha Vantage fallback), insider buying from OpenInsider
(SEC EDGAR fallback), analyst ratings from Finnhub, and fundamentals (P/E, market cap, float) from FMP. With a SentiSense key, the stock panel also shows the
SentiSense Rating (A–F letter, 0–100 score and rank, risk adjustments; a research score, not a buy/sell call)
and its top AI-generated insights. Keys are set in
Vercel → Settings → Environment Variables (see docs/SETUP.md).

Disclaimer: research tool only, not investment advice. Shariah screening is not a fatwa.
