# SEPA Terminal UI Bible

## Product character

The terminal is a calm, professional decision workspace: data-dense, low-glare, and explicit about state. It should feel closer to a well-made research product than a generic admin dashboard.

## Visual system

- Use semantic palette tokens (`bg`, `surface`, `ink`, `accent`, `up`, `down`, `warn`) so light and dark themes remain equivalent.
- Reserve accent blue for navigation, selection, and primary actions. Green and red communicate positive/negative market or trade state, not decoration.
- Cards use 12px radii, quiet borders, and restrained elevation. Nested content should prefer dividers or a tinted surface instead of more cards.
- Page titles are 22px; section titles are 13px; table and control text stays at 11–12px. Numeric data uses tabular figures.
- Dense tables keep sticky headers, clear row hover, a strong selected-row rail, and colour-coded outcomes where applicable.

## Layout and hierarchy

- The global header owns product identity, workspace navigation, market regime, scan date, theme, and command access.
- The scan-date picker opens directly to the selected session's month. Its month/year heading toggles a fast year-and-month chooser; unavailable months remain disabled. Its Today action returns to the latest completed scan session, never an empty calendar date.
- Pages begin with an eyebrow, title, and one-sentence purpose. The most decision-relevant content appears first.
- Overview uses a 12-column desktop grid. Setup Desk keeps the full queue beside a fixed-width decision panel. Symbol and Research pages use a shared 7xl content width.
- The terminal targets desktop research workflows and maintains a 960px minimum canvas so financial tables never silently discard columns.

## Interaction

- Every interactive control must have hover and keyboard-focus states.
- Scanner decisions are available both as visible actions and shortcuts; shortcuts never replace discoverable controls.
- Only dates backed by completed scan data are selectable and displayed.
- Charts always receive the complete available OHLCV history, while the default visible viewport is 1 year. The top-left range control offers 5Y, 2Y, 1Y, and 6M; historical signal views centre the chosen duration on the selected outcome so its band stays visible. Indicators are computed from the complete series, independent of the visible range.
- Signal links carry the exact signal date in the URL so symbol pages preserve which historical breakout the user selected across navigation and refreshes.
- Every Signal History row is a keyboard-accessible link into that same exact-signal flow; selecting a historical breakout immediately updates the chart band and matched trade outcome.
- A selected backtest trade renders a green/red holding-period band from breakout through exit, or through the latest available bar while open. Its on-chart label shows duration, return, and entry/end prices; clicking the band opens the complete trade detail card.
- Active positions remain `open` in the UI with mark-to-market return/R and no synthetic exit.
- A selected BREAKOUT renders a chart band only when the cached backtest contains a filled trade or an explicit `not_filled` record with a valid pending-window outcome. The latter is clearly labelled `NOT FILLED` and `HYPOTHETICAL — NOT REALISED P&L`.
- A raw signal absent from the cached backtest is labelled `NOT IN THIS BACKTEST` and never receives a fabricated outcome. Only an explicit `not_filled` result receives a backend-calculated pending-window outcome, rebased to the chart's OHLCV prices.
- Scanner and backtest actions operate on the configured full symbol universe.

## Copy

Use short, specific labels. Empty states explain what is absent and, when useful, what action creates it. Avoid promotional language and unexplained strategy claims.
