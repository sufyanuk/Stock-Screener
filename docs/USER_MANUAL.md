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
thresholds (1M volume change, IV rank, put/call, data source). The table lists the stocks in the latest file
that meet the criteria (and price < $100) — "Passes rules only" is ticked by default; untick it to see every
imported stock — sorted by 1-month options volume change, with price,
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

**Seasonality** — covers all US stocks. Pick a month with the month buttons and use the filters: Rank
by (% of years positive, or average positive change), minimum years of history, price range (default
$5 to $100) and ticker prefix. The table shows Ticker, Company, Price (current market price from Alpaca; if that is unavailable, the latest
daily close — hover to see which), Total Years, Total Positive Years,
% Positive, and the Max, Min and Average Positive Change (%) — the largest, smallest and average gain
in the years the month closed up. Every column can be sorted. Prices come from Alpaca monthly bars
(10 years, adjusted for splits and dividends) and load automatically the first time you open the tab
(a progress line shows "Loaded X of Y stocks"); after that the daily scan adds each new month, or click
**Update prices**. You can still import your own price-history CSV at the bottom of the page (months
already in the database are kept). Moves beyond ±200% in a month are ignored as data errors. Past
seasonality does not guarantee future returns.

**Shariah** — the internal Shariah screening engine. It reads actual figures from SEC filings for every
listed US stock (loaded automatically the first time you open the tab; the daily scan refreshes it weekly)
and classifies each stock as HALAL / SHARIAH COMPLIANT, NON-COMPLIANT or DOUBTFUL / INSUFFICIENT DATA under
each methodology separately: AAOIFI and FTSE. Pick a methodology, filter by result or ticker, and
click a row for the business screen, every ratio (figures, threshold, pass/fail) and the exact SEC filing
lines used. Missing data is never treated as compliant. FTSE's rules are built in from its official ground
rules; AAOIFI needs its thresholds entered under **Methodology rules** from the official AAOIFI Shari'ah
Standard No. 21 (until then it marks stocks DOUBTFUL). "Use … instead" chooses which methodology colours
tickers across the app (green = compliant, red = non-compliant, amber = doubtful, blue = not yet screened).

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

**Data loading** — data is fetched once per trading day (New York date) and kept in your browser: opening a
page again, or going back to filters you already used today, shows the saved results without contacting the
server. Changing a filter or criterion fetches that view once. Any change you make (running a scan, imports,
settings, Shariah rules) clears the saved results so the next view is fresh; "Refresh"/"Update" buttons always
fetch new data.

Disclaimer: research tool only, not investment advice. Shariah screening is not a fatwa.
