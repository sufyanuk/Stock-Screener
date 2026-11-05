# User manual

**Unusual Options** (opens first) — import the day's unusual-options CSV at the top of this tab
(choose the file, click **Preview** to check how it was read, e.g. +1,639% and IV rank 3.3, then
**Import**; import a new file each trading day). Below it, **Unusual-options criteria** sets the
thresholds (1M volume change, IV rank, put/call, data source). The table lists every stock in the latest
file, sorted by 1-month options volume change, with price, calls/puts, put/call and IV rank. "passes"
marks stocks meeting the criteria and price < $100; "Top 5" marks current recommendations.

**Insider Buying** — open-market purchases from SEC Form 4 filings over the last 7 / 30 / 60 / 90 days
(joint filings of one purchase shown once). **Insider-buying criteria** sets the % of shares outstanding,
look-back and basis used for the Top 5.

**Watchlist** — the Top 5 (stocks meeting every rule), near-qualified stocks failing one or two rules
(with reasons), and a **Custom watchlist** card: add any tickers (with an optional note) and each shows
its latest daily open, high, low, close, change and volume. Pick a stock in "Select stock" (or click its
row) to see the last 15 sessions. Prices come from your price API (Massive / FMP) when set, otherwise from
the imported price CSV.

**Bounces** — every stock in your price-history CSV that is turning up from:
- **its 52-week low**: the low was set within the last N sessions (default 10, not today) and the close is
  3–20% above it;
- **its 50-day EMA**: the EMA is rising, a daily low came within 1% of it in the last 3 sessions without
  closing more than 1% below it in the last 10 sessions, and the close is 0–5% above the EMA.
Both lists can require an up day and filter by price and 50-day average volume; all settings are on the tab.
Import a current price CSV (the same import as Seasonality, which includes the last 14 months) to refresh.

Every tab shows the last scan, a **Run scan now** button and warnings (missing/old options CSV, data issues).
Tickers are coloured by Shariah status everywhere: **green** = compliant, **red** = non-compliant,
blue = not yet screened. Shariah status does not affect which stocks qualify.

**Stock panel (right side)** — click any ticker on any screen to open it: price and day change, P/E,
float ratio, market cap, next earnings date, analyst ratings (how many recommend Buy / Strong Buy) with
the price target, a one-year chart, unusual-options and insider-buying context, and recent news.
Ctrl/Cmd-click a ticker (or "Full analysis →") for the full page. An amber **E** next to a ticker means
earnings are due within 7 days.

**Earnings** — companies reporting in the next 7 days with insider buying (open-market, last 90 days),
unusual options from the latest CSV, and the number of analysts rating the stock Strong Buy, Buy and
in total (loaded automatically from FMP, or Alpha Vantage as a fallback, and cached for a day). Filters show only names with insider buying or unusual options.

**Seasonality** — based on your own price-history CSV (daily, weekly or monthly OHLCV). On the
Seasonality or Bounces tab (one import updates both) choose the file (one file with a Symbol/Ticker column for all stocks, or one file per
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

**Calculators** — position size (long/short, fees, slippage, allocation cap, fractional shares) and
expectancy (R-multiples, profit factor, break-even win rate, Monte-Carlo risk of ruin and drawdown,
scenario grid).

**Data APIs** — the footer shows which data APIs are connected (● = key set). Stock prices come from
Alpaca (Finnhub fallback), price history from Tiingo (Alpha Vantage fallback), insider buying from SEC
EDGAR, analyst ratings from Finnhub, and fundamentals (P/E, market cap, float) from FMP. With a SentiSense key, the stock panel also shows the
SentiSense Rating (A–F letter, 0–100 score and rank, risk adjustments; a research score, not a buy/sell call)
and its top AI-generated insights. Keys are set in
Vercel → Settings → Environment Variables (see docs/SETUP.md).

Disclaimer: research tool only, not investment advice. Shariah screening is not a fatwa.
