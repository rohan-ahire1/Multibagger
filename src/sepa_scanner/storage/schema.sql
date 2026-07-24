CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    scan_date DATE NOT NULL,
    market_filter_on BOOLEAN,
    num_symbols BIGINT NOT NULL,
    num_eligible BIGINT NOT NULL,
    num_watch BIGINT NOT NULL,
    num_breakout BIGINT NOT NULL,
    status TEXT NOT NULL,
    error TEXT
);

ALTER TABLE runs ADD COLUMN IF NOT EXISTS num_trend_pass BIGINT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS num_rs_pass BIGINT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS num_setup_pass BIGINT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS num_near_pivot BIGINT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS bhavcopy_sha256 TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS bhavcopy_url TEXT;

CREATE TABLE IF NOT EXISTS downloads (
    download_id TEXT PRIMARY KEY,
    trade_date DATE NOT NULL,
    report_name TEXT,
    url TEXT,
    source TEXT,
    downloaded_at TIMESTAMP NOT NULL,
    http_status INTEGER,
    content_length BIGINT,
    etag TEXT,
    last_modified TEXT,
    file_path TEXT,
    sha256_zip TEXT,
    rows_total INTEGER,
    rows_after_series_filter INTEGER,
    rows_after_dedupe INTEGER,
    duplicates_dropped INTEGER,
    parse_notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_downloads_trade_date ON downloads(trade_date);

CREATE TABLE IF NOT EXISTS signals (
    signal_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    close DOUBLE NOT NULL,
    "pivot" DOUBLE NOT NULL,
    near_pivot DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    vol50 DOUBLE NOT NULL,
    vol_mult DOUBLE NOT NULL,
    rs_rank DOUBLE NOT NULL,
    range10 DOUBLE NOT NULL,
    voldry DOUBLE NOT NULL,
    atr_pct DOUBLE NOT NULL,
    adv50 DOUBLE NOT NULL,
    entry_ref DOUBLE NOT NULL,
    stop DOUBLE NOT NULL,
    r_per_share DOUBLE NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(signal_date, symbol, signal_type)
);

CREATE TABLE IF NOT EXISTS watchlist_candidates (
    watch_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    close DOUBLE NOT NULL,
    "pivot" DOUBLE NOT NULL,
    near_pivot DOUBLE NOT NULL,
    rs_rank DOUBLE NOT NULL,
    range10 DOUBLE NOT NULL,
    voldry DOUBLE NOT NULL,
    atr_pct DOUBLE NOT NULL,
    adv50 DOUBLE NOT NULL,
    vol50 DOUBLE NOT NULL,
    created_at TIMESTAMP NOT NULL,
    PRIMARY KEY (watch_date, symbol)
);

CREATE TABLE IF NOT EXISTS setup_reviews (
    scan_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    review_status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    reviewed_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    PRIMARY KEY (scan_date, symbol)
);

CREATE TABLE IF NOT EXISTS daily_diagnostics (
    diag_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    eligible BOOLEAN,
    tt_pass BOOLEAN,
    tt1_close_above_sma150 BOOLEAN,
    tt2_close_above_sma200 BOOLEAN,
    tt3_sma150_above_sma200 BOOLEAN,
    tt4_sma200_rising BOOLEAN,
    tt5_sma50_above_sma150 BOOLEAN,
    tt6_sma50_above_sma200 BOOLEAN,
    tt7_close_above_sma50 BOOLEAN,
    tt8_above_52w_low BOOLEAN,
    tt9_near_52w_high BOOLEAN,
    rs_rank DOUBLE,
    rs_pass BOOLEAN,
    setup_pass BOOLEAN,
    vcp1_range10_ok BOOLEAN,
    vcp2_voldry_ok BOOLEAN,
    vcp3_atr_trend_ok BOOLEAN,
    vcp4_atr_contract_ok BOOLEAN,
    vcp5_extension_ok BOOLEAN,
    vcp6_base_depth_ok BOOLEAN,
    contraction_count INTEGER,
    contraction_quality DOUBLE,
    "pivot" DOUBLE,
    near_pivot DOUBLE,
    setup_active_streak INTEGER,
    pivot_age_days INTEGER,
    watch_fail_mask INTEGER,
    breakout_fail_mask INTEGER,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(diag_date, symbol)
);

-- Speed up symbol-specific history charts and lookups
CREATE INDEX IF NOT EXISTS idx_diagnostics_symbol_date ON daily_diagnostics(symbol, diag_date);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_date ON signals(symbol, signal_date);

-- Speed up watchlist reviews lookup
CREATE INDEX IF NOT EXISTS idx_reviews_symbol_date ON setup_reviews(symbol, scan_date);

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
  status        TEXT NOT NULL,
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
  run_id           TEXT NOT NULL,
  d                DATE NOT NULL,
  equity           DOUBLE NOT NULL,
  drawdown_pct     DOUBLE NOT NULL,
  benchmark_equity DOUBLE
);

CREATE TABLE IF NOT EXISTS lab_trades (
  run_id      TEXT NOT NULL,
  symbol      TEXT NOT NULL,
  direction   TEXT NOT NULL,
  signal_date DATE,
  entry_date  DATE,
  exit_date   DATE,
  entry_price DOUBLE,
  exit_price  DOUBLE,
  shares      DOUBLE,
  stop        DOUBLE,
  r_multiple  DOUBLE,
  pnl         DOUBLE,
  pnl_pct     DOUBLE,
  mfe_r       DOUBLE,
  mae_r       DOUBLE,
  hold_days   INTEGER,
  exit_reason TEXT,
  status      TEXT,
  sector      TEXT,
  meta_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_lab_equity_run ON lab_equity(run_id);
CREATE INDEX IF NOT EXISTS idx_lab_trades_run ON lab_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_lab_runs_strategy ON lab_runs(strategy_id, created_at);
