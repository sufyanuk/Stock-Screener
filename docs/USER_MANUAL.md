# User manual

**Dashboard** — Daily Top 5 table (all the spec fields; click column headers to sort, click `?` to see the formula),
unusual options, latest Form-4 open-market buys, low-float names, the near-qualified watchlist with the reason each stock fails, and data-freshness/API health.
Tickers are coloured by Shariah status everywhere: **green** = compliant, **red** = non-compliant,
blue = not yet screened. Shariah status does not affect which stocks qualify.
**Run scan now** queues a manual scan. A yellow banner appears if an admin relaxed any requirement.

**Stock panel (right side)** — click any ticker on any screen to open it: price and day change, P/E,
float ratio, market cap, next earnings date, analyst ratings (how many recommend Buy / Strong Buy) with
the price target, a one-year chart, unusual-options and insider-buying context, and recent news.
Ctrl/Cmd-click a ticker (or "Full analysis →") for the full page. An amber **E** next to a ticker means
earnings are due within 7 days.

**Earnings** — companies reporting in the next 7 days with insider buying (open-market, last 90 days),
unusual options from the latest CSV, and analyst Strong Buy / Buy counts ("Load analyst counts" fetches
them; they are cached for a day). Filters show only names with insider buying or unusual options.

**Seasonality** — choose a list (latest options CSV, earnings next week, all scanned stocks, or your own
tickers), click **Update price history**, then pick a month: stocks are ranked by their average return in
that month across past years (or by % of years positive), with median, best/worst year, years of history
and a 12-month heatmap. "Top 5 for each month" shows the leaders for every month. Past seasonality does
not guarantee future returns.

**Stock page** — why the stock was or wasn't selected (pass/fail/missing per criterion, in plain
English); a candlestick chart with SMAs, support/resistance, entry, stop and targets; options
detail and largest contracts; insider purchases with joint-filing de-duplication; analyst
consensus and changes; float and liquidity; news; SEC
filings; and a data-provenance table. **Size this trade** opens the position calculator pre-filled.

**Calculators** — position size (long/short, fees, slippage, allocation cap, fractional shares),
expectancy (R-multiples, profit factor, break-even win rate, Monte-Carlo risk of ruin and drawdown,
scenario grid), and dividend purification.

**Journal** — record buys and sells (FIFO matching; a sell with no open long opens a short) and
dividends. Shows realized/unrealized P&L, fees, exposure, win rate, equity and drawdown curves,
daily-to-yearly breakdowns, and actual vs. recommended levels when you enter a recommendation ID.

**History** — every final daily Top 5 with return since recommendation, stop/target hits and revisions.

**Unusual options CSV (Admin page, top)** — choose the day's export, click **Preview** to check how
it was read (e.g. +1,639% and IV rank 3.3), then **Import**. The dashboard shows which file is in use and
warns when it is out of date. Import a new file each trading day before scanning.

**Admin** — thresholds, hard/soft toggles, weights, Shariah methodology, business reviews and notifications.

Disclaimer: research tool only, not investment advice. Shariah screening is not a fatwa.
