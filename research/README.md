# Historical research record

These JSON files are reviewed summaries of historical simulations. They contain no live practice account, credentials, model weights or cached vendor prices. They are bundled as read-only examples; a user's research commands write their own output to the local Nightdesk data directory.

The core recipe uses 65 price/volume features, eight forecasting components, 50 holdings, a top-200 holding buffer, ten-day score smoothing and industry/risk limits. The day-by-day S&P 500 membership reconstruction is preferable to using today's surviving companies, but Yahoo lacks prices for many historical members. That missing history still biases results.

Training proceeds on earlier dates, with gaps to prevent training targets from crossing the test boundary. Development years are 2010–2024. Since-2025 comparisons have been inspected for multiple veto decisions and are no longer an untouched holdout. Forward practice performance is the remaining honest prospective evaluation.

Costs are included, with double-cost and execution-timing sensitivity checks. Historical closing-price fills are simplified; an intraday decision before the close cannot know the final closing bar. Practice fills use fresh arriving prints and also simplify spreads, available size and market impact. Stop-loss behavior and real intraday execution do not exactly reproduce the daily backtest.

The default Steady simulation trailed the S&P 500 over the development period (roughly 12.4% versus 13.8% annually) with a smaller worst drawdown (28% versus 34%). Changing risk settings changes exposure; it is not evidence of better predictive skill. The research record preserves failed candidates and earlier flattering tests to make the differences inspectable.

`modes.json` contains current summarized comparisons. `grade_pit_*.json` contains fairer grades; `candidate_risk/` holds the rejected risk-adjusted candidate; `lab/` records experiments. Older today's-universe grades are historical records, not current claims.

Data and provider availability change. Reproducing a result with newly downloaded history is not guaranteed. These simulations are for research and education and are not financial advice.
