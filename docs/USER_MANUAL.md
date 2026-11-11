# User manual

**Scans and data refreshes** — the heavy work runs on GitHub Actions, not on Vercel (Vercel's free plan
allows only 4 CPU-hours a month). Every weekday evening after the US close (~21:40 UTC) a GitHub job scans
(insider buying, earnings calendar, analyst and SentiSense ratings, outstanding shares), refreshes the
Shariah prices and results, and analyst counts. The Shariah engine's SEC figures are downloaded again only
once a week, on Sunday (~04:17 UTC); weekday runs re-use the stored figures and never contact SEC. GitHub can delay or skip a
scheduled run, so each has a backup two to three hours later (~23:40 UTC weekdays, ~07:17 UTC Sunday) that
runs the job only if the main run did not happen that day. Opening a tab never starts a
load; each tab shows the stored data. Importing an Unusual Options CSV starts a scan on GitHub straight away
(it begins within about a minute and takes a few minutes; refresh the page for the results). Any job can also
be run by hand: GitHub → the repository → Actions → data-jobs → Run workflow. Every table column can be sorted by clicking its heading.

**Dashboard** (opens first) — near-qualified stocks failing one or two rules (with reasons; after a CSV import the
card updates by itself when the new scan finishes), and the top 5
from each tab: unusual options, insider buying (by % of outstanding
shares), upcoming earnings and seasonality for this month.
At the top, a **Market pulse** card (computed by the nightly job, so browsing costs nothing):
- **Market Mood** — SentiSense's 0–100 Market Mood (social sentiment, market direction, risk appetite, social
  momentum, S&P 500 trend, options flow): Extreme Fear ≤15, Fear ≤30, Anxiety ≤45, Neutral ≤55, Optimism ≤70,
  Greed ≤85, above that Extreme Greed. If SentiSense is unavailable, the app's own breadth gauge is used instead.
- **Macro backdrop** — −5…+5 from FRED data: 10-year yield trend, yield curve (10y−2y), high-yield credit
  spreads, the dollar and the Fed funds trend. ≤−2 Hostile, ≥+2 Supportive. Needs the free `FRED_API_KEY`.
- **Fed** — the next FOMC decision day (from federalreserve.gov) and days until it.
- **Breadth bar** — S&P 500 stocks advancing / declining / unchanged on the last session (SentiSense market
  heatmap; backup Alpaca). SentiSense has no macro score or Fed calendar, so those use FRED and the Fed's site.
"View all →" opens the full tab.

**Unusual Options** — import the day's unusual-options CSV at the top of this tab (the nightly run removes it after its scan has used it, so import a fresh one each day)
(choose the file, click **Preview** to check how it was read, e.g. +1,639% and IV rank 3.3, then
**Import**; the box shows a one-line summary; import a new file each trading day). Below it, **Unusual-options criteria** sets the
thresholds (1M volume change, IV rank, put/call, data source). The table lists the stocks in the latest file
that meet the criteria (and price < $100) — "Passes rules only" is ticked by default; untick it to see every
imported stock — sorted by 1-month options volume change, with price,
calls/puts, put/call and IV rank.

**Insider Buying** — the latest 100 open-market purchases (SEC Form 4 code P, filed in the last 2 years,
newest first), read straight from SEC EDGAR filings by the nightly GitHub job (free, no key; filings already
read are skipped). The lots of one filing are added together; OTC / unlisted stocks (not on NYSE, Nasdaq,
NYSE American or NYSE Arca) are left out. The 1% filter looks at every purchase filed in the last 90 days. **Only above 1% of outstanding shares** is ticked by default and keeps purchases where the
shares bought are more than 1% of the company's shares outstanding. Columns include Outstanding shares
and % of outstanding (shares bought ÷ shares outstanding). Share counts are looked up automatically and cached for 30 days, from (in order) the SEC figures already
loaded by the Shariah engine (balance sheet, filing cover page, quarterly weighted-average shares), Massive
ticker details, each company's SEC filings, then FMP / Finnhub / Alpha Vantage; a stock not found is retried. **Insider-buying
criteria** sets the % of shares outstanding, look-back and basis used for the Top 5.

**Watchlist** — your custom watchlist: add any tickers (with an optional note); each shows the current
trading day's open, high, low, close, change (vs the previous close) and volume only. Prices come from your
price APIs.

**Ratings** — two sub-tabs for the stocks the app follows (your watchlist, the latest options CSV, insider
purchases of the last 90 days, and earnings this week):
- **Analyst ratings — Strong Buy**: only stocks with at least one analyst rating Strong Buy (choose a higher
  minimum), with Buy / Hold / Sell / Strong Sell counts, total analysts and % Strong Buy (Finnhub; cached a day).
- **SentiSense rating**: A–F letter, 0–100 score, rank, risk points and favourable-analyst share; filter by
  letter. Loaded with SentiSense's screener (all rated stocks in one request) or per stock if the screener is not
  on your plan. A research score, not a buy/sell recommendation.

**Price column** — every table and dashboard card shows **Price** right after **Ticker** (or right after **Company**
when the table has a company column): the latest close
stored by the nightly price refresh (hover for its date), or the price in the imported CSV where a tab is based on
one (Unusual Options, Seasonality). On Insider Buying the insider's own purchase price is shown separately as
**Buy price**. A stock with no stored price shows "—".

**Stock side panel — Trade plan** — click any ticker: under the price figures, **Stop loss** and **Target 1** are
filled in from the daily chart by the same rules as the scan's trade plan (stop just below the nearest validated
support zone, i.e. zone low − ½ × ATR14; Target 1 = the next resistance zone), with their distance from the price and
the reward-to-risk ratio. Both can be edited; if there is no support zone, no stop is suggested. **Size this trade**
takes your account size and risk per trade (remembered on this device) and shows the shares to buy (risk ÷ (price −
stop), never more than the account can buy), the position value, the loss at the stop and the profit at Target 1.
Fees and slippage are not included (the Calculators tab handles those).

**Basket** — hover over any ticker anywhere in the app and click the **+** that appears to put it in the
basket (a **✓** shows it is already there; click it to take it out). The **Basket** tab (with a count) lists them
with company name and when each was added, and has **Clear basket**. The basket is kept on the server (same on
every device) until the next nightly scan, which empties it.

**Seasonality** — works only from the price-history CSV you import at the top of the tab; no price API
is used. When an import finishes, the statistics are calculated once and kept (in the database and the
browser's daily cache) until the next CSV import; opening the tab or changing filters only reads them. A
fresh import replaces the stored history; re-importing a few tickers replaces only those tickers. Pick a
month with the month buttons and use the filters: maximum price (default $100) and ticker prefix. Stocks are
ranked by % of years positive and need at least 5 years of history. The table shows
Ticker, Company, Price (the latest daily price already on file, else the last month-end close in your CSV
— hover to see the date), Total Years, Total Positive Years, % Positive, and the Max, Min and Average
Positive Change (%) — the largest, smallest and average gain in the years the month closed up. Every column
can be sorted. Moves beyond ±200% in a month are ignored as data errors. Past seasonality does not
guarantee future returns.

**Shariah** — the internal Shariah screening engine. It reads actual figures from SEC filings for every
listed US stock (loaded automatically the first time you open the tab; the daily scan refreshes it weekly)
and classifies each stock as HALAL / SHARIAH COMPLIANT, NON-COMPLIANT or DOUBTFUL / INSUFFICIENT DATA under
each methodology separately: AAOIFI and FTSE. Pick a methodology, filter by result or ticker, and
click a row for the business screen, every ratio (figures, threshold, pass/fail) and the exact SEC filing
lines used. Missing data is never treated as compliant. FTSE's rules are built in from its official ground
rules; AAOIFI's from Shari'ah Standard No. 21 (borrowings and interest-bearing deposits each ≤ 30% of market
capitalisation, prohibited income ≤ 5% of revenue). Both can be edited under **Methodology rules**. "Use … instead" chooses which methodology colours
tickers across the app (green = compliant, red = non-compliant, amber = doubtful, blue = not yet screened). Stocks that are NON-COMPLIANT under that methodology are hidden from every scan result (Top 5, Dashboard,
Unusual Options, Insider Buying, Earnings, Ratings, Seasonality); they still appear on the Shariah tab,
in stock lookups and in your custom watchlist.

**Stock page** — why the stock was or wasn't selected (pass/fail/missing per criterion, in plain
English); a candlestick chart with SMAs, support/resistance, entry, stop and targets; options
detail and largest contracts; insider purchases with joint-filing de-duplication; analyst
consensus and changes; float and liquidity; news; SEC
filings; and a data-provenance table. **Size this trade** opens the position calculator pre-filled.

**Calculators** — position size (account balance, risk %, entry, stop; optional fractional shares — a stop above the entry is a short) and
expectancy (R-multiples, profit factor, break-even win rate, Monte-Carlo risk of ruin and drawdown,
scenario grid).

**Data APIs** — the footer shows which data APIs are connected (● = key set). Stock prices come from
Alpaca (Finnhub fallback), price history from Tiingo (Alpha Vantage fallback), insider buying from SEC EDGAR Form 4
(OpenInsider optional with INSIDER_SOURCE=openinsider), analyst ratings from Finnhub, and fundamentals (P/E, market cap, float) from FMP. With a SentiSense key, the stock panel also shows the
SentiSense Rating (A–F letter, 0–100 score and rank, risk adjustments; a research score, not a buy/sell call)
and its top AI-generated insights. Keys are set in
Vercel → Settings → Environment Variables (see docs/SETUP.md).

**Data loading** — data is fetched once per trading day (New York date) and kept in your browser: opening a
page again, or going back to filters you already used today, shows the saved results without contacting the
server. Changing a filter or criterion fetches that view once. Any change you make (running a scan, imports,
settings, Shariah rules) clears the saved results so the next view is fresh; "Refresh"/"Update" buttons always
fetch new data.

Disclaimer: research tool only, not investment advice. Shariah screening is not a fatwa.


## One-time setup: GitHub Actions data jobs

1. **Secrets for the jobs** — GitHub → Stock-Screener → Settings → Secrets and variables → Actions →
   New repository secret. Add each of these with the same value as in Vercel (Vercel hides sensitive values,
   so copy them from each provider's dashboard): `DATABASE_URL` (Neon console → Connect → connection string),
   `SEC_USER_AGENT`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `FINNHUB_API_KEY`, `MASSIVE_API_KEY`,
   `FMP_API_KEY`, `TIINGO_API_KEY`, `ALPHAVANTAGE_API_KEY`, `SENTISENSE_API_KEY`, `FRED_API_KEY` (free:
   fred.stlouisfed.org → My Account → API Keys; powers the Dashboard's Macro backdrop). Only `DATABASE_URL` is
   required; a missing key just skips that data source.
2. **Token so the app can start a scan after a CSV import** — GitHub → your profile → Settings → Developer
   settings → Personal access tokens → Fine-grained tokens → Generate new token. Repository access: only
   Stock-Screener. Permissions: Actions → Read and write. Copy the token into Vercel → stock_screener →
   Settings → Environment Variables as `GITHUB_DISPATCH_TOKEN` (Production, Sensitive), then redeploy. For a codespace, add the same token
   as a Codespaces secret named `SCAN_DISPATCH_TOKEN` (Codespaces secret names cannot start with GITHUB).
   Without it, a CSV import runs the scan on Vercel instead.
3. **First run** — Actions → data-jobs → Run workflow → `nightly`. It loads everything (the first Shariah
   load takes longest); the log shows each step.

## Running the app in GitHub Codespaces (no installation, no admin rights)

1. Add your keys once: github.com → your profile picture → **Settings → Codespaces → Secrets → New secret**. Add
   `DATABASE_URL` (Neon console → Connect → connection pooling **off**), `JWT_SECRET` (any long random text),
   `SEC_USER_AGENT`, and the API keys (`ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `FINNHUB_API_KEY`, …). For each,
   under *Repository access* choose **Stock-Screener**.
2. Open github.com/sufyanuk/Stock-Screener → green **Code** button → **Codespaces** tab → **Create codespace on main**.
   The first start installs and builds the app (about 5 minutes); then the screener opens in a new browser tab
   (or open the **Ports** tab and click the globe icon next to port 3000).
3. Next time: Code → Codespaces → click your codespace; the app starts by itself. A codespace stops after about
   30 minutes without use. The address is private to your GitHub account.

## Running the app on your own computer

The same app can run locally against the same Neon database (e.g. while the Vercel site is paused). It shows the
stored data only — like the Vercel site, it never starts heavy loads; the GitHub jobs keep doing that.

1. Install **Docker Desktop** (docker.com) and **Git**, then: `git clone https://github.com/sufyanuk/Stock-Screener.git`
2. In the `Stock-Screener` folder, copy `.env.local.example` to `.env.local` and fill it in: `DATABASE_URL` from the Neon
   console (Connect → connection pooling **off**), a random `JWT_SECRET`, and the same API keys as in Vercel.
   `.env.local` is never committed.
3. `docker compose -f docker-compose.local.yml up -d --build` (the first build takes a few minutes), then open
   http://localhost:3000. Stop it with `docker compose -f docker-compose.local.yml down`; after a `git pull`, run step 3
   again to update.

Everything you change locally (imports, watchlist, settings) is saved in the shared database, so it also shows on
the Vercel site when that is back.
