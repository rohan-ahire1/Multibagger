use pyo3::prelude::*;
use numpy::PyReadonlyArray1;
use serde::{Deserialize, Serialize};
use chrono::NaiveDate;
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};
use duckdb::Connection;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum ExitMode {
    #[serde(rename = "v2_trail_10d")]
    V2Trail10d,
    #[serde(rename = "v2_trail_21ema")]
    V2Trail21ema,
    #[serde(rename = "v3_trail_pct")]
    V3TrailPct,
    #[serde(rename = "minervini_phased")]
    MinerviniPhased,
    #[serde(rename = "advanced_sepa")]
    AdvancedSepa,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RustBacktestParams {
    pub pending_days: usize,
    pub max_hold_days: usize,
    pub target_r: Option<f64>,
    pub exit_mode: ExitMode,
    pub trail_activation_r: f64,
    pub trail_pct: f64,
    pub dead_money_days: Option<usize>,
    pub friction_enabled: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RustSignal {
    pub symbol: String,
    pub sector: String,
    pub signal_date: NaiveDate,
    pub entry_ref: f64,
    pub stop: f64,
    pub pivot: Option<f64>,
    pub rs_rank: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RustBacktestTrade {
    pub symbol: String,
    pub signal_date: NaiveDate,
    pub status: String,
    pub entry_ref: f64,
    pub stop: f64,
    pub pivot: Option<f64>,
    pub entry_date: Option<NaiveDate>,
    pub entry_price: Option<f64>,
    pub exit_date: Option<NaiveDate>,
    pub exit_price: Option<f64>,
    pub holding_days: Option<usize>,
    pub return_pct: Option<f64>,
    pub r_multiple: Option<f64>,
    pub mae_price: Option<f64>,
    pub mfe_price: Option<f64>,
    pub exit_reason: Option<String>,
}

#[pyfunction]
fn health_check() -> String {
    "Multibagger Rust Core is online!".to_string()
}

#[pyfunction]
fn run_backtest_from_db_rust(
    db_path: String,
    params_json: String,
    all_dates: Vec<String>,
    opens: Vec<f64>,
    highs: Vec<f64>,
    lows: Vec<f64>,
    closes: Vec<f64>,
    symbol_indices: HashMap<String, (usize, usize)>,
) -> PyResult<String> {
    let params: RustBacktestParams = serde_json::from_str(&params_json).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

    let conn = Connection::open(&db_path).map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
    let mut stmt = conn.prepare("SELECT symbol, CAST(signal_date AS VARCHAR), entry_ref, stop, \"pivot\", rs_rank FROM signals WHERE signal_type = 'BREAKOUT'")
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

    let signal_iter = stmt.query_map([], |row| {
        let date_str: String = row.get(1)?;
        Ok(RustSignal {
            symbol: row.get(0)?,
            sector: "UNKNOWN".to_string(),
            signal_date: NaiveDate::parse_from_str(&date_str, "%Y-%m-%d").unwrap(),
            entry_ref: row.get(2)?,
            stop: row.get(3)?,
            pivot: row.get(4)?,
            rs_rank: row.get(5)?,
        })
    }).map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

    let mut signals = Vec::new();
    for signal in signal_iter {
        signals.push(signal.unwrap());
    }

    let parsed_dates: Vec<NaiveDate> = all_dates.iter()
        .map(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d").unwrap())
        .collect();

    let results: Vec<RustBacktestTrade> = signals.par_iter().map(|signal| {
        let (start, end) = match symbol_indices.get(&signal.symbol) {
            Some(indices) => *indices,
            None => return RustBacktestTrade::empty(signal, "missing_data"),
        };

        let sym_dates = &parsed_dates[start..end];
        let sym_opens = &opens[start..end];
        let sym_highs = &highs[start..end];
        let sym_lows = &lows[start..end];
        let sym_closes = &closes[start..end];

        simulate_single_signal_fast(signal, &params, sym_dates, sym_opens, sym_highs, sym_lows, sym_closes)
    }).collect();

    Ok(serde_json::to_string(&results).unwrap())
}

#[pyfunction]
fn run_bulk_backtest_rust_v2(
    signals_json: String,
    params_json: String,
    all_dates: Vec<String>,
    opens: PyReadonlyArray1<f64>,
    highs: PyReadonlyArray1<f64>,
    lows: PyReadonlyArray1<f64>,
    closes: PyReadonlyArray1<f64>,
    symbol_indices: HashMap<String, (usize, usize)>,
) -> PyResult<String> {
    let signals: Vec<RustSignal> = serde_json::from_str(&signals_json).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
    let params: RustBacktestParams = serde_json::from_str(&params_json).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

    let opens = opens.as_slice()?;
    let highs = highs.as_slice()?;
    let lows = lows.as_slice()?;
    let closes = closes.as_slice()?;

    let parsed_dates: Vec<NaiveDate> = all_dates.iter()
        .map(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d").unwrap())
        .collect();

    let results: Vec<RustBacktestTrade> = signals.par_iter().map(|signal| {
        let (start, end) = match symbol_indices.get(&signal.symbol) {
            Some(indices) => *indices,
            None => return RustBacktestTrade::empty(signal, "missing_data"),
        };

        let sym_dates = &parsed_dates[start..end];
        let sym_opens = &opens[start..end];
        let sym_highs = &highs[start..end];
        let sym_lows = &lows[start..end];
        let sym_closes = &closes[start..end];

        simulate_single_signal_fast(signal, &params, sym_dates, sym_opens, sym_highs, sym_lows, sym_closes)
    }).collect();

    Ok(serde_json::to_string(&results).unwrap())
}

fn simulate_single_signal_fast(
    signal: &RustSignal,
    params: &RustBacktestParams,
    dates: &[NaiveDate],
    opens: &[f64],
    highs: &[f64],
    lows: &[f64],
    closes: &[f64],
) -> RustBacktestTrade {
    let mut start_idx = None;
    for (i, &date) in dates.iter().enumerate() {
        if date > signal.signal_date {
            start_idx = Some(i);
            break;
        }
    }

    let start_idx = match start_idx {
        Some(idx) => idx,
        None => return RustBacktestTrade::empty(signal, "incomplete_data"),
    };

    let mut entry_idx = None;
    let mut entry_price = None;
    let pending_end = std::cmp::min(start_idx + params.pending_days, dates.len());

    for i in start_idx..pending_end {
        if opens[i] >= signal.entry_ref {
            entry_idx = Some(i);
            entry_price = Some(opens[i]);
            break;
        }
        if highs[i] >= signal.entry_ref {
            entry_idx = Some(i);
            entry_price = Some(signal.entry_ref);
            break;
        }
    }

    let entry_idx = match entry_idx {
        Some(idx) => idx,
        None => return RustBacktestTrade::empty(signal, "not_filled"),
    };
    let entry_p = entry_price.unwrap();
    let entry_date = dates[entry_idx];

    let friction = params.friction_enabled.unwrap_or(true);
    let make_trade = |status: &str, exit_d: NaiveDate, exit_p: f64, h_days: usize, mae: f64, mfe: f64| {
        make_single_signal_trade(signal, status, entry_date, entry_p, exit_d, exit_p, h_days, mae, mfe, friction, None)
    };

    let exit_end = std::cmp::min(entry_idx + params.max_hold_days, dates.len());
    let mut exit_p = closes[exit_end - 1];
    let mut exit_d = dates[exit_end - 1];
    let mut mae_price = entry_p;
    let mut mfe_price = entry_p;
    let risk = entry_p - signal.stop;
    let target_price = match params.target_r {
        Some(r) if risk > 0.0 => Some(entry_p + (risk * r)),
        _ => None,
    };

    let mut breakeven_activated = false;
    let mut trail_activated = false;
    let mut active_stop = signal.stop;

    for i in entry_idx..exit_end {
        let open = opens[i];
        let high = highs[i];
        let low = lows[i];
        let close = closes[i];
        let bar_date = dates[i];
        let holding_days = i - entry_idx + 1;
        let is_entry_day = i == entry_idx;

        mae_price = f64::min(mae_price, low);
        mfe_price = f64::max(mfe_price, high);

        if !is_entry_day && open <= active_stop {
            return make_trade("stopped", bar_date, open, holding_days, mae_price, mfe_price);
        }

        if let Some(target) = target_price {
            if !is_entry_day && open >= target {
                return make_trade("target", bar_date, open, holding_days, mae_price, mfe_price);
            }
        }

        let hit_stop = low <= active_stop;
        let hit_target = target_price.map_or(false, |t| high >= t);

        if hit_stop && hit_target {
            return make_trade("stopped", bar_date, active_stop, holding_days, mae_price, mfe_price);
        }
        if hit_stop {
            return make_trade("stopped", bar_date, active_stop, holding_days, mae_price, mfe_price);
        }
        if hit_target {
            return make_trade("target", bar_date, target_price.unwrap(), holding_days, mae_price, mfe_price);
        }

        if risk > 0.0 {
            if !breakeven_activated && high >= (entry_p + risk) {
                breakeven_activated = true;
                active_stop = f64::max(active_stop, entry_p);
            }
            if !trail_activated && high >= (entry_p + (risk * params.trail_activation_r)) {
                trail_activated = true;
            }

            if trail_activated {
                match params.exit_mode {
                    ExitMode::V2Trail10d => {
                        let lookback = 10;
                        if i >= lookback {
                            let min_low = lows[i-lookback..i].iter().fold(f64::INFINITY, |a, &b| a.min(b));
                            active_stop = f64::max(active_stop, min_low);
                        }
                    },
                    ExitMode::V3TrailPct => {
                        let trail_price = mfe_price * (1.0 - params.trail_pct);
                        active_stop = f64::max(active_stop, trail_price);
                    },
                    ExitMode::MinerviniPhased => {
                        let lookback = 10;
                        if i >= lookback {
                            let min_low = lows[i-lookback..i].iter().fold(f64::INFINITY, |a, &b| a.min(b));
                            active_stop = f64::max(active_stop, min_low);
                        }
                    },
                    ExitMode::AdvancedSepa => {
                        if high >= (entry_p + (risk * 3.0)) {
                            let lookback = 10;
                            if i >= lookback {
                                let min_low = lows[i-lookback..i].iter().fold(f64::INFINITY, |a, &b| a.min(b));
                                active_stop = f64::max(active_stop, min_low);
                            }
                            active_stop = f64::max(active_stop, entry_p + (risk * 2.0));
                        }
                    },
                    _ => {}
                }
            }
        }

        let reached_1r = risk > 0.0 && high >= (entry_p + risk);
        if reached_1r { breakeven_activated = true; }
        if let Some(dm_days) = params.dead_money_days {
            if !breakeven_activated && holding_days >= dm_days {
                return make_trade("dead_money", bar_date, close, holding_days, mae_price, mfe_price);
            }
        }

        if holding_days == params.max_hold_days {
            exit_p = close;
            exit_d = bar_date;
            break;
        }
    }

    make_trade("time_stop", exit_d, exit_p, exit_end - entry_idx, mae_price, mfe_price)
}

impl RustBacktestTrade {
    fn empty(sig: &RustSignal, status: &str) -> Self {
        Self {
            symbol: sig.symbol.clone(),
            signal_date: sig.signal_date,
            status: status.to_string(),
            entry_ref: sig.entry_ref,
            stop: sig.stop,
            pivot: sig.pivot,
            entry_date: None,
            entry_price: None,
            exit_date: None,
            exit_price: None,
            holding_days: None,
            return_pct: None,
            r_multiple: None,
            mae_price: None,
            mfe_price: None,
            exit_reason: None,
        }
    }

    fn close(
        sig: &RustSignal,
        status: &str,
        entry_date: NaiveDate,
        entry_price: f64,
        exit_date: NaiveDate,
        exit_price: f64,
        holding_days: usize,
        mae: f64,
        mfe: f64,
        return_pct: f64,
        r_multiple: Option<f64>,
        reason: Option<String>,
    ) -> Self {
        Self {
            symbol: sig.symbol.clone(),
            signal_date: sig.signal_date,
            status: status.to_string(),
            entry_ref: sig.entry_ref,
            stop: sig.stop,
            pivot: sig.pivot,
            entry_date: Some(entry_date),
            entry_price: Some(entry_price),
            exit_date: Some(exit_date),
            exit_price: Some(exit_price),
            holding_days: Some(holding_days),
            return_pct: Some(return_pct),
            r_multiple,
            mae_price: Some(mae),
            mfe_price: Some(mfe),
            exit_reason: reason,
        }
    }
}

fn make_single_signal_trade(
    signal: &RustSignal,
    status: &str,
    entry_date: NaiveDate,
    raw_entry: f64,
    exit_date: NaiveDate,
    raw_exit: f64,
    holding_days: usize,
    mae: f64,
    mfe: f64,
    friction_enabled: bool,
    reason: Option<String>,
) -> RustBacktestTrade {
    let (net_entry, net_exit, net_stop) = if friction_enabled {
        (raw_entry * 1.001 * 1.00118658, raw_exit * 0.999 * 0.99896342, signal.stop * 0.999 * 0.99896342)
    } else {
        (raw_entry, raw_exit, signal.stop)
    };
    let net_risk = net_entry - net_stop;
    let return_pct = if net_entry > 0.0 { (net_exit - net_entry) / net_entry * 100.0 } else { 0.0 };
    let r_multiple = if net_risk > 0.0 { Some((net_exit - net_entry) / net_risk) } else { None };

    RustBacktestTrade::close(
        signal,
        status,
        entry_date,
        net_entry,
        exit_date,
        net_exit,
        holding_days,
        mae,
        mfe,
        return_pct,
        r_multiple,
        reason,
    )
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RustPortfolioSettings {
    pub initial_capital: f64,
    pub risk_per_trade_pct: f64,
    pub max_positions: usize,
    pub max_sector_positions: usize,
    pub max_sector_pct: f64,
    pub max_heat_pct: f64,
    pub regime_heat_mult_strong: f64,
    pub drawdown_half_pct: f64,
    pub drawdown_stop_pct: f64,
    pub drawdown_close_pct: f64,
    pub allowed_entry_dates: Option<Vec<String>>,
}

#[derive(Debug, Clone)]
pub struct OpenPosition {
    pub signal: RustSignal,
    pub sector: String,
    pub entry_date: NaiveDate,
    pub entry_price: f64,
    pub shares: f64,
    pub position_value: f64,
    pub active_stop: f64,
    pub initial_risk: f64,
    pub target_price: Option<f64>,
    pub mae_price: f64,
    pub mfe_price: f64,
    pub breakeven_activated: bool,
    pub trail_activated: bool,
}

#[derive(Debug, Clone)]
pub struct PendingSignal {
    pub signal: RustSignal,
    pub entry_ref: f64,
    pub expiration_date: NaiveDate,
}

#[derive(Serialize)]
struct PortfolioResult {
    trades: Vec<RustBacktestTrade>,
    initial_capital: f64,
    final_equity: f64,
}

fn has_known_sector(sector: &str) -> bool {
    let normalized = sector.trim().to_ascii_uppercase();
    !normalized.is_empty() && normalized != "UNKNOWN"
}

#[pyfunction]
fn run_portfolio_backtest_rust(
    signals_json: String,
    params_json: String,
    portfolio_json: String,
    all_dates: Vec<String>,
    opens: PyReadonlyArray1<f64>,
    highs: PyReadonlyArray1<f64>,
    lows: PyReadonlyArray1<f64>,
    closes: PyReadonlyArray1<f64>,
    symbol_indices: HashMap<String, (usize, usize)>,
) -> PyResult<String> {
    let mut signals: Vec<RustSignal> = serde_json::from_str(&signals_json).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
    let params: RustBacktestParams = serde_json::from_str(&params_json).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
    let pf_settings: RustPortfolioSettings = serde_json::from_str(&portfolio_json).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

    let friction_enabled = params.friction_enabled.unwrap_or(true);
    let slippage_buy = if friction_enabled { 1.001 } else { 1.0 };
    let slippage_sell = if friction_enabled { 0.999 } else { 1.0 };
    let tax_buy = if friction_enabled { 1.00118658 } else { 1.0 };
    let tax_sell = if friction_enabled { 0.99896342 } else { 1.0 };
    let dp_charge = if friction_enabled { 16.0 } else { 0.0 };

    signals.sort_by(|a, b| a.signal_date.cmp(&b.signal_date).then_with(|| a.symbol.cmp(&b.symbol)));

    let opens = opens.as_slice()?;
    let highs = highs.as_slice()?;
    let lows = lows.as_slice()?;
    let closes = closes.as_slice()?;

    let parsed_dates: Vec<NaiveDate> = all_dates.iter()
        .map(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d").unwrap())
        .collect();
    let allowed_entry_dates: Option<HashSet<NaiveDate>> = pf_settings.allowed_entry_dates.as_ref().map(|dates| {
        dates
            .iter()
            .filter_map(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d").ok())
            .collect()
    });

    let mut signals_by_date: HashMap<NaiveDate, Vec<RustSignal>> = HashMap::new();
    for sig in signals {
        signals_by_date.entry(sig.signal_date).or_insert_with(Vec::new).push(sig);
    }

    let mut unique_dates: Vec<NaiveDate> = parsed_dates.clone();
    unique_dates.sort();
    unique_dates.dedup();

    let mut cash = pf_settings.initial_capital;
    let mut equity_peak = pf_settings.initial_capital;
    let mut open_positions: Vec<OpenPosition> = Vec::new();
    let mut sector_position_counts: HashMap<String, usize> = HashMap::new();
    let mut closed_trades: Vec<RustBacktestTrade> = Vec::new();
    let mut pending_signals: Vec<PendingSignal> = Vec::new();

    for &today in &unique_dates {
        let mut i = 0;
        while i < open_positions.len() {
            let pos = &mut open_positions[i];
            
            let (start, end) = match symbol_indices.get(&pos.signal.symbol) {
                Some(&indices) => indices,
                None => { i += 1; continue; }
            };
            
            let sym_dates = &parsed_dates[start..end];
            
            if let Some(idx) = sym_dates.iter().position(|&d| d == today) {
                let open = opens[start + idx];
                let high = highs[start + idx];
                let low = lows[start + idx];
                let close = closes[start + idx];
                
                let holding_days = (today - pos.entry_date).num_days() as usize; 
                
                pos.mae_price = f64::min(pos.mae_price, low);
                pos.mfe_price = f64::max(pos.mfe_price, high);
                
                let mut exit_price = None;
                let mut status = "";
                
                if open <= pos.active_stop {
                    exit_price = Some(open);
                    status = "stopped";
                } else if let Some(target) = pos.target_price {
                    if open >= target {
                        exit_price = Some(open);
                        status = "target";
                    } else if high >= target && low <= pos.active_stop {
                        exit_price = Some(pos.active_stop);
                        status = "stopped";
                    } else if low <= pos.active_stop {
                        exit_price = Some(pos.active_stop);
                        status = "stopped";
                    } else if high >= target {
                        exit_price = Some(target);
                        status = "target";
                    }
                } else if low <= pos.active_stop {
                    exit_price = Some(pos.active_stop);
                    status = "stopped";
                }
                
                if exit_price.is_none() && holding_days >= params.max_hold_days {
                    exit_price = Some(close);
                    status = "time_stop";
                }
                
                if let Some(ep) = exit_price {
                    let net_entry = pos.entry_price * slippage_buy * tax_buy;
                    let net_exit = ep * slippage_sell * tax_sell;
                    let net_stop = pos.signal.stop * slippage_sell * tax_sell;
                    let net_risk = net_entry - net_stop;
                    let pnl = (pos.shares * net_exit - dp_charge) - (pos.shares * net_entry);
                    let return_pct = if net_entry > 0.0 { (pnl / (pos.shares * net_entry)) * 100.0 } else { 0.0 };
                    let r_multiple = if net_risk > 0.0 { Some(pnl / (pos.shares * net_risk)) } else { None };

                    cash += pos.shares * net_exit - dp_charge;
                    if has_known_sector(&pos.sector) {
                        let should_remove = if let Some(count) = sector_position_counts.get_mut(&pos.sector) {
                            *count = count.saturating_sub(1);
                            *count == 0
                        } else {
                            false
                        };
                        if should_remove {
                            sector_position_counts.remove(&pos.sector);
                        }
                    }
                    closed_trades.push(RustBacktestTrade::close(
                        &pos.signal,
                        status,
                        pos.entry_date,
                        net_entry,
                        today,
                        net_exit,
                        holding_days,
                        pos.mae_price,
                        pos.mfe_price,
                        return_pct,
                        r_multiple,
                        None,
                    ));
                    open_positions.remove(i);
                    continue; 
                }
                
                let risk = pos.entry_price - pos.signal.stop;
                if risk > 0.0 {
                    if !pos.breakeven_activated && high >= (pos.entry_price + risk) {
                        pos.breakeven_activated = true;
                        pos.active_stop = f64::max(pos.active_stop, pos.entry_price);
                    }
                    if !pos.trail_activated && high >= (pos.entry_price + (risk * params.trail_activation_r)) {
                        pos.trail_activated = true;
                    }
                    if pos.trail_activated {
                        if params.exit_mode == ExitMode::AdvancedSepa {
                            if high >= (pos.entry_price + (risk * 3.0)) {
                                let lookback = 10;
                                if idx >= lookback {
                                    let min_low = lows[start + idx - lookback..start + idx].iter().fold(f64::INFINITY, |a, &b| a.min(b));
                                    pos.active_stop = f64::max(pos.active_stop, min_low);
                                }
                                pos.active_stop = f64::max(pos.active_stop, pos.entry_price + (risk * 2.0));
                            }
                        } else if params.exit_mode == ExitMode::V3TrailPct {
                            let trail_price = pos.mfe_price * (1.0 - params.trail_pct);
                            pos.active_stop = f64::max(pos.active_stop, trail_price);
                        }
                    }
                }
                
                let reached_1r = risk > 0.0 && high >= (pos.entry_price + risk);
                if reached_1r { pos.breakeven_activated = true; }
                if let Some(dm_days) = params.dead_money_days {
                    if !pos.breakeven_activated && holding_days >= dm_days {
                        let net_entry = pos.entry_price * slippage_buy * tax_buy;
                        let net_exit = close * slippage_sell * tax_sell;
                        let net_stop = pos.signal.stop * slippage_sell * tax_sell;
                        let net_risk = net_entry - net_stop;
                        let pnl = (pos.shares * net_exit - dp_charge) - (pos.shares * net_entry);
                        let return_pct = if net_entry > 0.0 { (pnl / (pos.shares * net_entry)) * 100.0 } else { 0.0 };
                        let r_multiple = if net_risk > 0.0 { Some(pnl / (pos.shares * net_risk)) } else { None };

                        cash += pos.shares * net_exit - dp_charge;
                        if has_known_sector(&pos.sector) {
                            let should_remove = if let Some(count) = sector_position_counts.get_mut(&pos.sector) {
                                *count = count.saturating_sub(1);
                                *count == 0
                            } else {
                                false
                            };
                            if should_remove {
                                sector_position_counts.remove(&pos.sector);
                            }
                        }
                        closed_trades.push(RustBacktestTrade::close(
                            &pos.signal,
                            "dead_money",
                            pos.entry_date,
                            net_entry,
                            today,
                            net_exit,
                            holding_days,
                            pos.mae_price,
                            pos.mfe_price,
                            return_pct,
                            r_multiple,
                            None,
                        ));
                        open_positions.remove(i);
                        continue;
                    }
                }
            }
            i += 1;
        }

        let mut pos_value = 0.0;
        for pos in &open_positions {
            let (start, end) = *symbol_indices.get(&pos.signal.symbol).unwrap();
            let sym_dates = &parsed_dates[start..end];
            let raw_price = if let Some(idx) = sym_dates.iter().position(|&d| d == today) {
                closes[start + idx]
            } else {
                pos.entry_price
            };
            pos_value += pos.shares * raw_price * slippage_sell * tax_sell;
        }
        let current_equity = cash + pos_value;
        equity_peak = f64::max(equity_peak, current_equity);

        if let Some(daily_signals) = signals_by_date.get(&today) {
            for sig in daily_signals {
                let exp_date = today + chrono::Duration::days(params.pending_days as i64);
                pending_signals.push(PendingSignal {
                    signal: sig.clone(),
                    entry_ref: sig.entry_ref,
                    expiration_date: exp_date,
                });
            }
        }

        let dd_pct = if equity_peak > 0.0 { (equity_peak - current_equity) / equity_peak } else { 0.0 };
        let size_mult = if dd_pct >= pf_settings.drawdown_close_pct {
            0.0
        } else if dd_pct >= pf_settings.drawdown_stop_pct {
            0.0
        } else if dd_pct >= pf_settings.drawdown_half_pct {
            0.5
        } else {
            1.0
        };
        let entries_allowed_today = allowed_entry_dates
            .as_ref()
            .map_or(true, |dates| dates.contains(&today));

        let mut j = 0;
        while j < pending_signals.len() {
            if pending_signals[j].expiration_date < today {
                let sig = &pending_signals[j].signal;
                closed_trades.push(RustBacktestTrade::empty(sig, "not_filled"));
                pending_signals.remove(j);
                continue;
            }

            if !entries_allowed_today {
                j += 1;
                continue;
            }

            if size_mult == 0.0 || open_positions.len() >= pf_settings.max_positions {
                let sig = &pending_signals[j].signal;
                closed_trades.push(RustBacktestTrade::empty(sig, "skipped_portfolio"));
                pending_signals.remove(j);
                continue;
            }

            if current_equity <= 0.0 {
                let sig = &pending_signals[j].signal;
                closed_trades.push(RustBacktestTrade::empty(sig, "skipped_portfolio"));
                pending_signals.remove(j);
                continue;
            }

            let p_sig = &pending_signals[j];
            let (start, end) = match symbol_indices.get(&p_sig.signal.symbol) {
                Some(&indices) => indices,
                None => {
                    let sig = &pending_signals[j].signal;
                    closed_trades.push(RustBacktestTrade::empty(sig, "missing_data"));
                    pending_signals.remove(j);
                    continue;
                }
            };
            let sym_dates = &parsed_dates[start..end];

            let mut filled = false;
            if let Some(idx) = sym_dates.iter().position(|&d| d == today) {
                let open = opens[start + idx];
                let high = highs[start + idx];

                if open >= p_sig.entry_ref || high >= p_sig.entry_ref {
                    let fill_price = f64::max(open, p_sig.entry_ref);
                    
                    let risk_budget = current_equity * pf_settings.risk_per_trade_pct * size_mult;
                    let total_heat: f64 = open_positions.iter().map(|pos| pos.initial_risk * pos.shares).sum();
                    
                    let is_allowed_date = allowed_entry_dates.as_ref().map_or(true, |set| set.contains(&today));
                    let current_max_heat = if is_allowed_date {
                        pf_settings.max_heat_pct * pf_settings.regime_heat_mult_strong
                    } else {
                        pf_settings.max_heat_pct
                    };

                    if ((total_heat + risk_budget) / current_equity) > current_max_heat {
                        let sig = &pending_signals[j].signal;
                        closed_trades.push(RustBacktestTrade::empty(sig, "skipped_portfolio"));
                        pending_signals.remove(j);
                        continue;
                    }

                    let net_entry = fill_price * slippage_buy * tax_buy;
                    let net_stop = p_sig.signal.stop * slippage_sell * tax_sell;
                    let net_risk = net_entry - net_stop;

                    if net_risk > 0.0 {
                        let shares = risk_budget / net_risk;
                        let cost = shares * net_entry;

                        if has_known_sector(&p_sig.signal.sector) {
                            let sector_count = sector_position_counts
                                .get(&p_sig.signal.sector)
                                .copied()
                                .unwrap_or(0);
                            if sector_count >= pf_settings.max_sector_positions {
                                let sig = &pending_signals[j].signal;
                                closed_trades.push(RustBacktestTrade::empty(sig, "skipped_portfolio"));
                                pending_signals.remove(j);
                                continue;
                            }

                            let sector_value: f64 = open_positions
                                .iter()
                                .filter(|pos| pos.sector == p_sig.signal.sector)
                                .map(|pos| pos.position_value)
                                .sum();
                            if ((sector_value + cost) / current_equity) > pf_settings.max_sector_pct {
                                let sig = &pending_signals[j].signal;
                                closed_trades.push(RustBacktestTrade::empty(sig, "skipped_portfolio"));
                                pending_signals.remove(j);
                                continue;
                            }
                        }

                        if cost <= cash {
                            cash -= cost;
                            let target_price = match params.target_r {
                                Some(r) => Some(fill_price + ((fill_price - p_sig.signal.stop) * r)),
                                None => None,
                            };

                            open_positions.push(OpenPosition {
                                signal: p_sig.signal.clone(),
                                sector: p_sig.signal.sector.clone(),
                                entry_date: today,
                                entry_price: fill_price,
                                shares,
                                position_value: cost,
                                active_stop: p_sig.signal.stop,
                                initial_risk: fill_price - p_sig.signal.stop,
                                target_price,
                                mae_price: fill_price,
                                mfe_price: fill_price,
                                breakeven_activated: false,
                                trail_activated: false,
                            });
                            if has_known_sector(&p_sig.signal.sector) {
                                *sector_position_counts.entry(p_sig.signal.sector.clone()).or_insert(0) += 1;
                            }
                            filled = true;
                        }
                    }
                }
            }

            if filled {
                pending_signals.remove(j);
            } else {
                j += 1;
            }
        }
    }
    
    if let Some(&last_date) = unique_dates.last() {
        for pos in open_positions {
            let (start, end) = *symbol_indices.get(&pos.signal.symbol).unwrap();
            let sym_dates = &parsed_dates[start..end];
            let mut final_price = pos.entry_price;
            if let Some(idx) = sym_dates.iter().position(|&d| d == last_date) {
                final_price = closes[start + idx];
            }
            let holding_days = (last_date - pos.entry_date).num_days() as usize;

            let net_entry = pos.entry_price * slippage_buy * tax_buy;
            let net_exit = final_price * slippage_sell * tax_sell;
            let net_stop = pos.signal.stop * slippage_sell * tax_sell;
            let net_risk = net_entry - net_stop;
            let pnl = (pos.shares * net_exit - dp_charge) - (pos.shares * net_entry);
            let return_pct = if net_entry > 0.0 { (pnl / (pos.shares * net_entry)) * 100.0 } else { 0.0 };
            let r_multiple = if net_risk > 0.0 { Some(pnl / (pos.shares * net_risk)) } else { None };

            cash += pos.shares * net_exit - dp_charge;
            closed_trades.push(RustBacktestTrade::close(
                &pos.signal,
                "open",
                pos.entry_date,
                net_entry,
                last_date,
                net_exit,
                holding_days,
                pos.mae_price,
                pos.mfe_price,
                return_pct,
                r_multiple,
                None,
            ));
        }
    }

    let final_result = PortfolioResult {
        trades: closed_trades,
        initial_capital: pf_settings.initial_capital,
        final_equity: cash, // At the very end, all positions were liquidated into cash
    };

    Ok(serde_json::to_string(&final_result).unwrap())
}



/// A massive optimization engine that runs a grid search entirely in parallel Rust.
#[pyfunction]
fn run_optimization_grid_rust(
    signals_json: String,
    params_grid_json: String, // List of BacktestParams
    all_dates: Vec<String>,
    opens: PyReadonlyArray1<f64>,
    highs: PyReadonlyArray1<f64>,
    lows: PyReadonlyArray1<f64>,
    closes: PyReadonlyArray1<f64>,
    symbol_indices: HashMap<String, (usize, usize)>,
) -> PyResult<String> {
    let signals: Vec<RustSignal> = serde_json::from_str(&signals_json).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
    let params_grid: Vec<RustBacktestParams> = serde_json::from_str(&params_grid_json).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

    let opens = opens.as_slice()?;
    let highs = highs.as_slice()?;
    let lows = lows.as_slice()?;
    let closes = closes.as_slice()?;

    let parsed_dates: Vec<NaiveDate> = all_dates.iter()
        .map(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d").unwrap())
        .collect();

    // Prepare internal symbol data pointers
    let mut sym_data_map = HashMap::new();
    for (sym, (start, end)) in symbol_indices {
        sym_data_map.insert(sym, (start, end));
    }

    // Run the entire grid search in parallel
    // Outer loop: Parameters, Inner loop: Signals
    let results: Vec<(usize, f64, f64)> = params_grid.par_iter().enumerate().map(|(p_idx, params)| {
        let mut total_r = 0.0;
        let mut count = 0;
        
        for signal in &signals {
            if let Some(&(start, end)) = sym_data_map.get(&signal.symbol) {
                let trade = simulate_single_signal_fast(
                    signal, params, 
                    &parsed_dates[start..end], 
                    &opens[start..end], 
                    &highs[start..end], 
                    &lows[start..end], 
                    &closes[start..end]
                );
                
                if let Some(r) = trade.r_multiple {
                    total_r += r;
                    count += 1;
                }
            }
        }
        
        let expectancy = if count > 0 { total_r / (count as f64) } else { 0.0 };
        (p_idx, expectancy, total_r)
    }).collect();

    Ok(serde_json::to_string(&results).unwrap())
}

#[pymodule]
fn multibagger_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(health_check, m)?)?;
    m.add_function(wrap_pyfunction!(run_backtest_from_db_rust, m)?)?;
    m.add_function(wrap_pyfunction!(run_bulk_backtest_rust_v2, m)?)?;
    m.add_function(wrap_pyfunction!(run_portfolio_backtest_rust, m)?)?;
    m.add_function(wrap_pyfunction!(run_optimization_grid_rust, m)?)?;
    Ok(())
}
