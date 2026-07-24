# Generic Strategy Lab — turn multibagger into a multi-strategy tester

## Summary

Today the repo has one first-class strategy (SEPA breakout) wired end-to-end through
`src/sepa_scanner/` (scan → DuckDB `signals` → `backtest/simulation.py` → `/api/backtest/run` →
`sepa-terminal/src/pages/Backtest.tsx`), plus four strategies living as isolated scripts under
`strategies/` (`mean_reversion`, `episodic_pivots`, `daily_factors`, `autonomous_research`). Each
of those four re-implements its own position/trade dataclasses, its own portfolio simulator, its
own friction/tax math, its own metrics, and writes its own CSV/JSON into `strategies/*/output/`.
Nothing about them is reachable from the UI, and their numbers are not comparable to each other.

This plan introduces a **Strategy Lab**: one strategy contract, two simulation engines behind a
single result shape, a registry, persisted run history in DuckDB, and a UI surface that lists
registered strategies, generates a parameter form from metadata, runs backtests as background
jobs, and compares runs on one leaderboard and one overlaid equity chart.

Adding a new strategy after this lands = write one Python module implementing the contract and
decorate it with `@register_strategy`. It appears in the UI automatically with a parameter form,
runs on the shared engine, and lands on the same leaderboard.

### Non-goals

- No authoring of strategy logic from the UI (no code editor, no rule DSL, no `exec`). New
  strategies are Python modules in the repo.
- No parameter sweeps / Optuna from the UI in this plan. The existing `optimize_*.py` scripts stay
  as they are. The lab's run-history schema is designed so a sweep can later write many runs, but
  no sweep UI is built here.
- No changes to live/paper trading (`src/sepa_scanner/live/`,
  `strategies/mean_reversion/live/`).
- The existing `/api/backtest/*` routes and `/backtest` page are **not** removed. They remain the
  SEPA-specific deep-dive (signal scorecard, signal-quality analysis). The lab is additive.

### Hard constraint: parity

`docs/BACKTEST_TRUTH.md` is the governing document. It states the canonical SEPA numbers
(NSE bhavcopy panel, signals through 2026-03-17): **CAGR 12.48% / MaxDD 24.28% / Calmar 0.51 /
281 trades**, reproducible via `uv run python optimize_joint_wf.py --parity`. The SEPA strategy
running through the new lab runner must reproduce these figures exactly. This is enforced by an
automated test, not by eyeballing. If parity cannot be achieved, the lab does not ship for SEPA.

`BACKTEST_TRUTH.md` also says "do not add new backtest scripts". The lab is not a new script — it
is the consolidation that removes four of them. `BACKTEST_TRUTH.md` must be updated (see
Implementation Changes, step 9) to name the lab runner as the canonical multi-strategy harness
while keeping `optimize_joint_wf.py --parity` as the SEPA canonical check.

---

## Architecture

New package `src/sepa_scanner/lab/`:

```
lab/
├─ __init__.py            # public exports
├─ contracts.py           # Strategy protocol, StrategyKind, ParamSpec, StrategyContext
├─ registry.py            # @register_strategy, get_strategy, list_strategies, discovery
├─ result.py              # BacktestRunResult, TradeRecord, EquityPoint, RunMetrics
├─ metrics.py             # canonical metrics, computed from the daily equity curve only
├─ friction.py            # unified India cost model (brokerage/STT/stamp/exchange/GST/DP/slippage)
├─ panel.py               # OHLCV panel loading + universe resolution + benchmark loading
├─ signal_cache.py        # parquet cache of generated signals keyed by params hash
├─ engines/
│  ├─ __init__.py
│  ├─ signal_engine.py    # adapter over backtest/simulation.simulate_portfolio_python
│  └─ rebalance_engine.py # extracted from strategies/daily_factors/engine.py
├─ runner.py              # orchestration: resolve → signals → engine → metrics → persist
├─ jobs.py                # in-process job queue + status
└─ store.py               # DuckDB persistence for runs/equity/trades

src/sepa_scanner/lab_strategies/    # the registered strategy modules
├─ __init__.py            # imports every module so decorators fire
├─ sepa_breakout.py
├─ mean_reversion.py
├─ episodic_pivots.py
├─ daily_factors.py
└─ research_momentum.py
```

`strategies/` is kept on disk during migration (the modules in `lab_strategies/` import the signal
logic from there where practical rather than copy-pasting it), and its now-dead simulators are
removed in the final cleanup step.

### Strategy contract (`lab/contracts.py`)

```python
class StrategyKind(StrEnum):
    SIGNAL = "signal"        # discrete entries with a stop; per-trade exits; R-multiples
    REBALANCE = "rebalance"  # periodic target weights; no per-trade stop

@dataclass(frozen=True, slots=True)
class ParamSpec:
    name: str
    label: str
    type: Literal["int", "float", "bool", "enum", "date", "str"]
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: list[str] | None = None
    group: str = "General"       # UI form grouping
    help: str = ""
    advanced: bool = False       # collapsed by default in the UI

@dataclass(slots=True)
class StrategyContext:
    settings: AppSettings
    panel: pl.DataFrame          # full OHLCV panel for the requested window + warmup
    benchmark: pl.DataFrame
    sector_map: dict[str, str]
    trading_days: list[date]
    data_dir: Path

class Strategy(Protocol):
    id: str                      # stable slug, e.g. "sepa_breakout" — persisted, never renamed
    name: str
    kind: StrategyKind
    description: str
    param_specs: list[ParamSpec]

    def warmup_days(self, params: dict) -> int: ...
    def universe(self, params: dict) -> Literal["config"] | list[str]: ...

    # kind == SIGNAL
    def generate_signals(self, ctx: StrategyContext, params: dict) -> pl.DataFrame: ...

    # kind == REBALANCE
    def target_weights(self, ctx: StrategyContext, params: dict) -> pl.DataFrame: ...

    def engine_config(self, params: dict) -> dict: ...   # portfolio/exec/friction overrides
```

**Signal frame contract** (columns, enforced by a validator in `runner.py`):

| column | type | required | meaning |
|---|---|---|---|
| `symbol` | str | yes | NSE symbol as it appears in the panel |
| `signal_date` | date | yes | EOD date the signal fired; entry is never same-day |
| `direction` | str | no (default `"long"`) | `"long"` \| `"short"` |
| `entry_ref` | f64 | yes | trigger/reference price |
| `stop` | f64 | yes | initial stop price |
| `rank_score` | f64 | no | higher = better; used by `max_signals_per_day` truncation |
| `sector` | str | no | filled from `sector_map` when absent |
| `meta` | str (JSON) | no | strategy-specific fields surfaced verbatim in the UI trade table |

**Target-weight frame contract**:

| column | type | required |
|---|---|---|
| `rebalance_date` | date | yes |
| `symbol` | str | yes |
| `weight` | f64 | yes (sum per date ≤ 1.0; validator rejects >1.0 + 1e-6) |

### Engines

Both engines return the same `BacktestRunResult`. Metrics for both are computed by the **same**
function from the **daily equity curve** — never from serially compounded R. This is what makes a
factor strategy and a breakout strategy legitimately comparable, and it is what
`BACKTEST_TRUTH.md` already demands for SEPA.

- **`engines/signal_engine.py`** — a thin adapter over
  **`scripts.portfolio_diagnostics.simulate_portfolio_python`** (`scripts/portfolio_diagnostics.py:499`).
  The existing function's behaviour is **not changed** in steps 1–4; the adapter's only job is to
  build `BacktestParams`, the portfolio dict, `exit_config`, and the regime
  `entry_gate`/`entry_size_mult` from the merged strategy-params + config defaults, then map the
  returned dict into `BacktestRunResult`. This is the parity guarantee: SEPA through the lab
  executes the identical code path as `optimize_joint_wf.py --parity`.

  > **There are two divergent copies of `simulate_portfolio_python` in this repo. Adapt the
  > `scripts/portfolio_diagnostics.py` one — it is the canonical engine.**
  >
  > `optimize_joint_wf.py:43` does `import scripts.portfolio_diagnostics as diag` and calls
  > `diag.simulate_portfolio_python` at line 153. The copy at
  > `src/sepa_scanner/backtest/simulation.py:251` — which `api/routes/backtest.py` imports and which
  > therefore powers the current `/backtest` UI page — is a **stale fork** that cannot reproduce the
  > canonical numbers. Confirmed divergences:
  >
  > | | `portfolio_diagnostics.py:499` | `backtest/simulation.py:251` |
  > |---|---|---|
  > | function length | ~900 lines (499–1397) | ~518 lines (251–769) |
  > | `friction_enabled` getattr default | `False` | `True` |
  > | ETF sector-cap exemption (`:570`) | present | absent |
  > | idle-cash yield sleeve (`cash_yield_apy`) | present | absent |
  > | pyramid / `entry_day_collision_mode` refs | 22 | 3 |
  > | extra exit-tuning kwargs in signature | 9 | 0 |
  >
  > The missing `cash_yield_apy` support matters beyond tidiness: the 17.20% total-portfolio row in
  > `BACKTEST_TRUTH.md` is produced by that code. The fork cannot express it. Step 14 converges the
  > two; until then, do not treat any number from `backtest/simulation.py` as canonical.

  As part of step 1, move the canonical simulator out of `scripts/` into
  `src/sepa_scanner/lab/engines/portfolio_sim.py` **by pure file move with no edits**, and leave
  `scripts/portfolio_diagnostics.py` importing it from the new location so
  `optimize_joint_wf.py --parity` keeps working unchanged. A simulator that only exists under
  `scripts/` cannot be depended on by library code without an import inversion.

  Short support (needed by `mean_reversion`) is added to `simulate_portfolio_python` in step 5, as
  a separately-gated change with its own regression tests, *after* SEPA parity is locked. Long-only
  behaviour must be bit-identical before and after that change — enforced by re-running the parity
  test.

- **`engines/rebalance_engine.py`** — extracted from `strategies/daily_factors/engine.py`
  (`simulate_rebalanced_portfolio` and its helpers). Executes target weights at the next open after
  each rebalance date, applies the shared friction model, marks to market daily, emits `orders`
  instead of `trades`. Since it *is* the daily_factors engine, its numbers must match
  `strategies/daily_factors/output/summary.json` exactly after extraction.

### Unified result shape (`lab/result.py`)

```python
@dataclass(slots=True)
class RunMetrics:
    # equity-curve derived — computed identically for both engines
    cagr_pct: float
    total_return_pct: float
    terminal_equity: float
    max_drawdown_pct: float
    max_drawdown_days: int
    calmar: float
    sharpe: float
    sortino: float
    volatility_pct: float
    exposure_pct: float          # avg fraction of capital deployed
    benchmark_cagr_pct: float | None
    excess_cagr_pct: float | None
    # trade-derived — None for REBALANCE kind
    trades: int | None
    win_rate: float | None
    avg_r: float | None
    expectancy_r: float | None
    payoff_ratio: float | None
    avg_hold_days: float | None
    turnover_pct: float | None   # populated for both kinds

@dataclass(slots=True)
class BacktestRunResult:
    run_id: str
    strategy_id: str
    params: dict
    params_hash: str
    start_date: date
    end_date: date
    kind: StrategyKind
    data_source: str             # e.g. "nse_bhavcopy"
    metrics: RunMetrics
    equity_curve: list[EquityPoint]   # date, equity, drawdown_pct, benchmark_equity
    trades: list[TradeRecord]         # or orders for REBALANCE
    extras: dict                      # strategy-specific; rendered generically as key/value
    warnings: list[str]
```

`TradeRecord` is a superset with nullable fields (`stop`, `r_multiple`, `mfe_r`, `mae_r`,
`exit_reason` are null for rebalance orders) plus a `meta` dict. The UI picks the visible columns
from `kind`.

### Persistence

Signals are computed on demand and cached, so a parameter change is immediately backtestable
without re-running the nightly scan.

- Signal cache: `data/lab/signals/{strategy_id}/{params_hash}_{start}_{end}.parquet`, with an index
  row in DuckDB. `params_hash` = sha256 of the canonical JSON of `{strategy_id, params,
  data_source, panel_fingerprint}` truncated to 16 hex chars. `panel_fingerprint` = mtime+size of
  the source parquet, so a data refresh invalidates the cache automatically.
- Runs: new tables appended to `src/sepa_scanner/storage/schema.sql`.

```sql
CREATE TABLE IF NOT EXISTS lab_runs (
  run_id        TEXT PRIMARY KEY,
  strategy_id   TEXT NOT NULL,
  label         TEXT,
  params_json   TEXT NOT NULL,
  params_hash   TEXT NOT NULL,
  start_date    DATE NOT NULL,
  end_date      DATE NOT NULL,
  kind          TEXT NOT NULL,
  data_source   TEXT NOT NULL,
  status        TEXT NOT NULL,      -- queued|running|done|failed|cancelled
  progress_pct  DOUBLE DEFAULT 0,
  stage         TEXT,
  error         TEXT,
  metrics_json  TEXT,
  extras_json   TEXT,
  warnings_json TEXT,
  created_at    TIMESTAMP NOT NULL,
  finished_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lab_equity (
  run_id TEXT NOT NULL, d DATE NOT NULL,
  equity DOUBLE NOT NULL, drawdown_pct DOUBLE NOT NULL, benchmark_equity DOUBLE
);

CREATE TABLE IF NOT EXISTS lab_trades (
  run_id TEXT NOT NULL, symbol TEXT NOT NULL, direction TEXT NOT NULL,
  signal_date DATE, entry_date DATE, exit_date DATE,
  entry_price DOUBLE, exit_price DOUBLE, shares DOUBLE, stop DOUBLE,
  r_multiple DOUBLE, pnl DOUBLE, pnl_pct DOUBLE, mfe_r DOUBLE, mae_r DOUBLE,
  hold_days INTEGER, exit_reason TEXT, status TEXT, sector TEXT, meta_json TEXT
);

CREATE TABLE IF NOT EXISTS lab_signal_cache (
  strategy_id TEXT NOT NULL, params_hash TEXT NOT NULL,
  start_date DATE NOT NULL, end_date DATE NOT NULL,
  path TEXT NOT NULL, row_count INTEGER, computed_at TIMESTAMP NOT NULL,
  PRIMARY KEY (strategy_id, params_hash, start_date, end_date)
);

CREATE INDEX IF NOT EXISTS idx_lab_equity_run ON lab_equity(run_id);
CREATE INDEX IF NOT EXISTS idx_lab_trades_run ON lab_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_lab_runs_strategy ON lab_runs(strategy_id, created_at);
```

DuckDB `initialize()` already executes the whole of `schema.sql` with `IF NOT EXISTS`, so appending
these statements is the entire migration — no separate migration script.

### Jobs

Full-history runs take minutes; the existing UI already stretches its POST timeout to 300s, which
does not survive a comparison workflow. Runs therefore become background jobs:

- `lab/jobs.py` holds a module-level `JobRunner` with a `ThreadPoolExecutor(max_workers=1)`
  (configurable via `config.yaml: lab.max_concurrent_runs`, default 1 — the panel is large and
  concurrent runs risk memory pressure). The single worker also keeps DuckDB writes serialised.
- The run row is inserted with `status='queued'` synchronously so the UI can render it immediately;
  the worker flips it to `running`, updates `progress_pct`/`stage` at coarse checkpoints
  (`loading_panel`, `generating_signals`, `simulating`, `computing_metrics`, `persisting`), then
  `done`/`failed`.
- Jobs are in-process only. On API restart, any row left in `queued`/`running` is marked
  `failed` with error `"interrupted by restart"` on `initialize()`.

### API (`src/sepa_scanner/api/routes/lab.py`)

| method | path | purpose |
|---|---|---|
| GET | `/api/lab/strategies` | registry: id, name, kind, description, param specs |
| GET | `/api/lab/strategies/{id}/defaults` | resolved defaults merged with `config.yaml` |
| POST | `/api/lab/runs` | `{strategy_id, params, start_date, end_date, label?}` → `{run_id, status}` |
| GET | `/api/lab/runs` | leaderboard list: id, strategy, label, period, status, metrics |
| GET | `/api/lab/runs/{run_id}` | full `BacktestRunResult` |
| GET | `/api/lab/runs/{run_id}/status` | `{status, progress_pct, stage, error}` — polled every 2s |
| DELETE | `/api/lab/runs/{run_id}` | delete run + its equity/trade rows |
| POST | `/api/lab/compare` | `{run_ids: [...]}` → date-aligned normalised curves + metrics table |
| GET | `/api/lab/benchmarks` | available benchmark symbols from config |

`POST /api/lab/runs` validates `params` against the strategy's `param_specs` (unknown key →
422; out-of-range → 422) before enqueueing. Compare aligns curves on the intersection of run date
ranges and rebases every series to 100 at the first common date; runs with no overlap are returned
in a `disjoint` list rather than silently dropped.

### UI (`sepa-terminal/`)

New route `/lab` (hotkey `g>l`, added to `App.tsx`, `TopBar`, `CommandPalette`, `ShortcutOverlay`)
with three tabs, plus a detail route `/lab/runs/:runId`.

1. **Strategies tab** — card grid from `GET /api/lab/strategies`. Selecting one opens a
   right-hand panel with an auto-generated form: `ParamSpec.type` maps to control
   (`int`/`float` → number input with min/max/step, `bool` → toggle, `enum` → select, `date` →
   date input, `str` → text), grouped by `ParamSpec.group`, `advanced: true` specs behind a
   collapsed "Advanced" disclosure. Plus period pickers, an optional run label, and **Run
   backtest**. On submit → POST, then navigate to the run detail page which polls status.
2. **Runs tab** — the leaderboard. Sortable table (strategy, label, period, CAGR, MaxDD, Calmar,
   Sharpe, trades, status, created). Checkbox selection of 2–6 runs enables **Compare**. Row click
   → detail. Filter by strategy and by status.
3. **Compare tab** — overlaid rebased equity curves (one line per run + benchmark), a shared
   drawdown chart, and a side-by-side metrics table with best-in-column highlighting.

**Run detail page** (`/lab/runs/:runId`) generalises the current `Backtest.tsx` layout: header with
strategy + params summary, metrics grid from `RunMetrics` (trade-derived tiles hidden when null),
equity + drawdown chart, trades/orders table whose columns depend on `kind`, `meta` expandable per
row, and an `extras` key/value section. While `status !== 'done'` it renders a progress state
driven by the status poll.

New `sepa-terminal/src/api/lab.ts` (hooks) and `src/lib/lab-types.ts` (types) — the existing
`snakeToCamel` transform in `client.ts` applies unchanged. Charts use `recharts`, already a
dependency. Styling follows `docs/UI_BIBLE.md`; light theme is the default.

### CLI

Add a `lab` sub-app to `src/sepa_scanner/main.py`:

- `sepa lab list` — registered strategies + param specs
- `sepa lab run <strategy_id> --start ... --end ... [--param k=v ...] [--label ...]` — runs
  synchronously in the foreground, persists to the same tables, prints the metrics table
- `sepa lab runs [--strategy ...]` — leaderboard in the terminal
- `sepa lab show <run_id>` — metrics + top/bottom trades

---

## Implementation Changes

Ordered; each step is independently reviewable and leaves the repo working.

1. **`lab/` skeleton and contracts.** Add `contracts.py`, `registry.py`, `result.py`,
   `metrics.py`, `panel.py`, `signal_cache.py`, `store.py`, `runner.py`, `jobs.py` and the
   `engines/` package. `metrics.py` computes every equity-derived metric from the daily curve;
   port the existing CAGR/MaxDD/Calmar definitions from `optimize_joint_wf.py`'s parity path
   verbatim so SEPA numbers do not shift. Append the four tables to `storage/schema.sql`. No
   strategy is registered yet. Registry discovery = explicit imports in
   `lab_strategies/__init__.py` (no filesystem scanning, no dynamic import — keeps failures loud
   and startup deterministic).

2. **`lab/friction.py`.** Extract the India cost model. `strategies/episodic_pivots/ep_engine.py`
   has the most complete version (`brokerage`, `buy_charges`, `sell_charges`, STT/DP handling) —
   move it here, parameterised by a `FrictionConfig` sourced from `config.yaml` (`backtest.slippage_bps`
   plus a new `friction:` block carrying the statutory rates that are currently hardcoded).
   The canonical `simulate_portfolio_python` must keep producing identical friction figures after
   this — assert it with a unit test on the existing charge values (`1.00118658` buy, `0.99896342`
   sell, `16.0` DP, `slippage_bps`) before touching anything else. Note the two copies disagree on
   the `friction_enabled` getattr fallback (`False` canonical vs `True` in the fork); the extracted
   model must preserve the **canonical** `False` fallback.

3. **`engines/signal_engine.py`.** Adapter over the canonical `simulate_portfolio_python` (now at
   `lab/engines/portfolio_sim.py` after the step-1 move), mapping its returned dict (`trades`,
   `open_trades`, `pending_trades`, `daily_equity`) into `BacktestRunResult`. No behavioural change
   to the simulator. The adapter must pass through the canonical version's extra keyword arguments
   (`breakeven_activation_r`, `time_stop_days`, `partial_profit_r`, `trail_pct_bull`,
   `trail_pct_bear`, `trail_widen_r`, `entry_day_collision_mode`) — sourced from config, not
   hardcoded — since omitting them silently selects different defaults than the parity run uses.

4. **`lab_strategies/sepa_breakout.py` + parity gate.** Implement `generate_signals` by calling the
   existing scan path (`strategy/signal_engine.py`) over the panel for the requested window, then
   applying `backtest.simulation.filter_signals` with the config-sourced ranking/RS bounds — i.e.
   exactly what `api/routes/backtest.py` does today, but without reading the DuckDB `signals`
   table. Expose as `ParamSpec`s: `rs_rank_min`, `rs_rank_max`, `max_signals_per_day`,
   `pending_days`, `max_hold_days`, `exit_mode`, `baseline_tighten_r`, `stagnation_exit_bars`,
   `stagnation_max_mfe_r`, `stop_trigger_mode`, `signal_exit_execution`, `slippage_bps`,
   `risk_per_trade_pct`, `max_positions`, `max_sector_positions`, `contraction_max_count`, plus the
   regime-gate toggles — every default read from `config.yaml`, never hardcoded.
   **Gate:** `tests/lab/test_sepa_parity.py` runs the lab over 2010-01-04 → 2026-03-17 with the
   committed config and asserts CAGR ≈ 12.48%, MaxDD ≈ 24.28%, trades == 281 (tolerance 0.01pp on
   rates, exact on trade count). Marked `@pytest.mark.slow`. **Do not proceed to step 5 until this
   passes.**

5. **Short support in the signal engine.** Add `direction` handling to
   `simulate_portfolio_python`: mirrored stop/target/R math, mirrored exit triggers, sizing on
   absolute risk-per-share, no borrow cost (documented assumption). Long-only paths must be
   untouched — re-run the step-4 parity test as the regression gate.

6. **`lab_strategies/mean_reversion.py`.** `generate_signals` delegates to
   `strategies/mean_reversion/signal_engine.scan_mean_reversion` /
   `scan_short_mean_reversion` / `scan_mr_v2` (selected by a `variant` enum param), emitting the
   contract frame. Params come from `strategies/mean_reversion/config.yaml`. Its bespoke
   `simulate_mr_portfolio` / `simulate_mr_long_short_portfolio` are retired in favour of the shared
   engine. **Gate:** a comparison test runs both old and new sims on a fixed window and asserts
   metrics within 5% relative; any residual delta must be explained in a `## Migration deltas`
   section appended to `strategies/mean_reversion/README.md` (exit-model differences are expected
   and acceptable; unexplained differences are not).

7. **`lab_strategies/episodic_pivots.py`.** Same pattern: `generate_signals` wraps
   `ep_engine.build_features` + `make_signals`; `EPConfig` fields become `ParamSpec`s; its own
   `simulate` is retired. **Gate:** metrics compared against
   `strategies/episodic_pivots/output/summary.json`, same 5%/explained-delta rule.

8. **`engines/rebalance_engine.py` + `lab_strategies/daily_factors.py`.** Move
   `simulate_rebalanced_portfolio`, `_cap_weights`, `_equal_weights`, `_price_maps` and the
   performance-metric helpers out of `strategies/daily_factors/engine.py` into the engine; the
   strategy module implements `target_weights` by wrapping `load_and_build_features` +
   `build_targets`, with `ResearchConfig` fields exposed as `ParamSpec`s (including a `factor` enum
   covering the existing `momentum_12_1_top20`, `high_52w_top20`, `alpha_low_vol_top30`,
   `high_52w_alpha_low_vol_top30`, `equal_weight_universe` presets). **Gate:** exact match against
   `strategies/daily_factors/output/summary.json` — this is a pure extraction, so any difference is
   a bug.

9. **`lab_strategies/research_momentum.py`.** Register the best configuration produced by
   `strategies/autonomous_research/` (per its `output/summary.json`) as a normal SIGNAL strategy.
   The research harness itself stays a script; it is a search process, not a strategy.

10. **API + jobs.** Add `api/routes/lab.py`, register in `api/app.py`, wire `lab/jobs.py`,
    and mark orphaned `queued`/`running` rows failed on startup. Leave `routes/backtest.py`
    untouched.

11. **UI.** `src/lib/lab-types.ts`, `src/api/lab.ts`, `src/pages/Lab.tsx` (three tabs),
    `src/pages/LabRun.tsx` (detail), `src/components/lab/ParamForm.tsx`,
    `src/components/lab/EquityCompareChart.tsx`, `src/components/lab/RunsTable.tsx`. Routes and
    hotkey in `App.tsx`; entries in `TopBar`, `CommandPalette`, `ShortcutOverlay`.

12. **CLI.** `sepa lab` sub-app in `main.py` as specified above.

13. **Docs + cleanup.** Update `docs/BACKTEST_TRUTH.md` with a "Strategy Lab" section naming the
    lab runner as the canonical multi-strategy harness and restating that
    `optimize_joint_wf.py --parity` remains the SEPA canonical check (and that the lab must match
    it). Update `docs/ARCHITECTURE.md`, `docs/BACKTESTING.md`, `README.md` (project layout + a
    "Adding a strategy" section), and `CLAUDE.md`. Delete the now-dead simulators from
    `strategies/mean_reversion/simulation.py`, `strategies/episodic_pivots/ep_engine.py`, and
    `strategies/daily_factors/engine.py`, keeping their signal/feature code that
    `lab_strategies/` imports. `strategies/*/output/` artefacts stay as historical reference and are
    referenced by the migration-delta notes.

---

## Test Plan

**Unit** (`tests/lab/`)

- `test_registry.py` — duplicate id rejected; unknown id raises; every registered strategy has a
  non-empty `id`/`name`/`description` and at least one `ParamSpec`; ids are stable slugs.
- `test_param_validation.py` — unknown key rejected; below-min/above-max rejected; enum outside
  `choices` rejected; defaults round-trip; bool/int/float coercion from JSON.
- `test_metrics.py` — CAGR/MaxDD/Calmar/Sharpe/Sortino/exposure against hand-computed curves,
  including a flat curve, a single-day curve, and an all-loss curve (no div-by-zero, no NaN).
- `test_signal_contract.py` — validator rejects missing `symbol`/`signal_date`/`entry_ref`/`stop`,
  wrong dtypes, `stop >= entry_ref` for a long, duplicate `(symbol, signal_date)`; weight frame
  validator rejects per-date weight sums > 1.0.
- `test_signal_cache.py` — same params hit cache; changed param misses; changed panel fingerprint
  invalidates.
- `test_friction.py` — extracted charge model reproduces the pre-extraction values for a set of
  known buy/sell turnovers.
- `test_store.py` — run insert/update/fetch round-trip; delete cascades to equity and trades;
  orphaned run cleanup on init.

**Parity / golden** (`tests/lab/`, `@pytest.mark.slow`)

- `test_sepa_parity.py` — the step-4 gate. CAGR 12.48% / MaxDD 24.28% / 281 trades.
- `test_sepa_long_only_unchanged.py` — re-runs the parity assertion after the short-support change.
- `test_daily_factors_extraction.py` — exact match vs `strategies/daily_factors/output/summary.json`.
- `test_mean_reversion_migration.py`, `test_episodic_pivots_migration.py` — within 5% relative on
  CAGR/MaxDD/trade count vs the recorded outputs; failures require a documented delta.

**API** (`tests/api/test_lab_routes.py`)

- `GET /api/lab/strategies` returns every registered strategy with serialisable param specs.
- `POST /api/lab/runs` with bad params → 422 and no row inserted; with good params → 202-style
  payload with a `run_id` and a `queued` row.
- Status transitions queued → running → done on a tiny 3-month window with a stub strategy.
- `GET /api/lab/runs/{id}` on an unfinished run returns status without a result body; on a missing
  run → 404.
- `POST /api/lab/compare` aligns curves, rebases to 100, and reports disjoint runs separately.
- A deliberately failing stub strategy produces `status='failed'` with the error recorded, not a
  500 on the polling endpoint.

**UI**

- `npm run build` and `tsc --noEmit` clean in `sepa-terminal/`.
- Manual: strategy list renders; param form generates correct controls per type and rejects
  out-of-range input client-side; run submits and the detail page shows progress then results;
  leaderboard sorts; selecting 3 runs renders 3 rebased curves plus benchmark; rebalance-kind run
  detail hides R-multiple columns and shows orders.

**Regression**

- Full existing suite (`tests/`) green — in particular the existing backtest tests must not move,
  since `simulate_portfolio_python` is only extended, never re-tuned.
- `uv run python optimize_joint_wf.py --parity` still prints the canonical numbers after the
  friction extraction and the short-support change.

---

## Assumptions

- Price source is the committed `config.data.source: nse_bhavcopy` panel
  (`data/nse_adjusted/ohlcv_all.parquet`). The lab records `data_source` per run so runs on
  different panels are never silently compared; the compare endpoint warns when selected runs
  disagree on `data_source`.
- Shorting assumes zero borrow cost and unrestricted availability. This is optimistic for NSE cash
  segment and is recorded as a `warning` on any run containing short trades.
- Single-user, localhost-only deployment. No auth, no multi-tenant concerns, in-process job queue is
  sufficient. DuckDB stays single-writer.
- `run_id` is a ULID-style sortable string; `strategy_id` slugs are permanent once persisted
  (renaming one orphans its history).
- Existing `strategies/*/output/` numbers are treated as the reference truth for migration deltas
  even though they were produced by non-canonical engines — they are a regression baseline, not a
  claim of correctness.
- Benchmark for excess-return metrics is `config.market.benchmark_symbol`, with the Nifty 500 TRI
  series used where already available (`strategies/*/output/*nifty500tri*`).

## Risks

- **Parity drift (high impact).** Any change to `simulate_portfolio_python` risks moving the
  canonical SEPA numbers. Mitigated by making step 4 a hard gate and re-running it after step 5;
  the adapter must not "improve" anything.
- **Mean-reversion migration is the real work.** It is long/short and has its own exit model; the
  shared engine may not express it exactly. If the 5% band cannot be met with a documented reason,
  the fallback is to keep `mean_reversion` on its own engine behind the same contract (a third
  engine kind) rather than distorting either the strategy or the shared engine.
- **Run duration.** Full-history runs on the full panel may exceed several minutes and hold a large
  panel in memory. Mitigated by the single-worker queue, the signal cache, and panel loading scoped
  to the requested window plus warmup.
- **Metric redefinition.** Existing per-strategy READMEs quote metrics computed by their own math.
  After migration the lab's numbers are authoritative and will differ; the migration-delta notes
  must say so explicitly so old numbers are not re-quoted.

## Acceptance Criteria

1. `GET /api/lab/strategies` lists five strategies, each with a complete parameter spec.
2. SEPA run through the lab reproduces `BACKTEST_TRUTH.md` canonical numbers, asserted by an
   automated test.
3. `daily_factors` run through the lab matches its recorded `summary.json` exactly.
4. `mean_reversion` and `episodic_pivots` runs are within 5% of their recorded metrics, or the
   difference is documented in their READMEs.
5. From the UI alone: pick a strategy, edit its parameters, run a backtest, watch progress, view the
   report, and overlay it against runs from two other strategies on one chart.
6. Adding a sixth strategy requires exactly one new module in `lab_strategies/` plus its import
   line — no changes to the engine, API, or UI.
7. `optimize_joint_wf.py --parity` and the pre-existing test suite are unaffected.
