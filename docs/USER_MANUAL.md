# User manual

**Unusual Options** (opens first) — every stock in your latest imported CSV, sorted by 1-month options
volume change, with price, calls/puts, put/call and IV rank. "passes" marks stocks meeting the options
rules and price < $100; "Top 5" marks current recommendations. Filter by ticker/name or "Passes rules only".

**Insider Buying** — open-market purchases from SEC Form 4 filings over the last 7 / 30 / 60 / 90 days
(joint filings of one purchase shown once).

**Watchlist** — the Top 5 (stocks meeting every rule) and near-qualified stocks that fail one or two rules,
with the reasons.

Every tab shows the last scan, a **Run scan now** button and warnings (missing/old options CSV, data issues).
Tickers are coloured by Shariah status everywhere: **green** = compliant, **red** = non-compliant,
blue = not yet screened. Shariah status does not affect which stocks qualify.

**Stock panel (right side)** — click any ticker on any screen to open it: price and day change, P/E,
float ratio, market cap, next earnings date, analyst ratings (how many recommend Buy / Strong Buy) with
the price target, a one-year chart, unusual-options and insider-buying context, and recent news.
Ctrl/Cmd-click a ticker (or "Full analysis →") for the full page. An amber **E** next to a ticker means
earnings are due within 7 days.

**Earnings** — companies reporting in the next 7 days with insider buying (open-market, last 90 days),
unusual options from the latest CSV, and analyst Strong Buy / Buy counts ("Load analyst counts" fetches
them; they are cached for a day). Filters show only names with insider buying or unusual options.

**Seasonality** — covers all US stocks. Click **Load price history** once: it fetches the month-end
close of every stock for the last 10 years (one request per month, about 120 in total), then the daily
closing scan keeps it current. Pick a month to rank stocks by average return in that month (or % of
years positive), with median, best/worst year, years of data, latest price and a 12-month heatmap.
Filter by price range, minimum years and ticker prefix. "Top 5 for each month" shows the leaders for
every month. Price returns only; moves beyond ±200% in a month are ignored as data errors. Past
seasonality does not guarantee future returns.

**Stock page** — why the stock was or wasn't selected (pass/fail/missing per criterion, in plain
English); a candlestick chart with SMAs, support/resistance, entry, stop and targets; options
detail and largest contracts; insider purchases with joint-filing de-duplication; analyst
consensus and changes; float and liquidity; news; SEC
filings; and a data-provenance table. **Size this trade** opens the position calculator pre-filled.

**Calculators** — position size (long/short, fees, slippage, allocation cap, fractional shares) and
expectancy (R-multiples, profit factor, break-even win rate, Monte-Carlo risk of ruin and drawdown,
scenario grid).

**Unusual options CSV (Admin page, top)** — choose the day's export, click **Preview** to check how
it was read (e.g. +1,639% and IV rank 3.3), then **Import**. The dashboard shows which file is in use and
warns when it is out of date. Import a new file each trading day before scanning.

**Admin** — thresholds, hard/soft toggles, weights, Shariah methodology, business reviews and notifications.

Disclaimer: research tool only, not investment advice. Shariah screening is not a fatwa.
