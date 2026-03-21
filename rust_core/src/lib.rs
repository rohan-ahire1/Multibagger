use pyo3::prelude::*;
use numpy::PyReadonlyArray1;
use serde::{Deserialize, Serialize};
use chrono::NaiveDate;
use rayon::prelude::*;
use std::collections::HashMap;
use duckdb::Connection;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum ExitMode {
    #[serde(rename = "v1_fixed")]
    V1Fixed,
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
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RustSignal {
    pub symbol: String,
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
            return RustBacktestTrade::close(signal, "stopped", entry_date, entry_p, bar_date, open, holding_days, mae_price, mfe_price, None);
        }

        if let Some(target) = target_price {
            if !is_entry_day && open >= target {
                return RustBacktestTrade::close(signal, "target", entry_date, entry_p, bar_date, open, holding_days, mae_price, mfe_price, None);
            }
        }

        let hit_stop = low <= active_stop;
        let hit_target = target_price.map_or(false, |t| high >= t);

        if hit_stop && hit_target {
            return RustBacktestTrade::close(signal, "stopped", entry_date, entry_p, bar_date, active_stop, holding_days, mae_price, mfe_price, None);
        }
        if hit_stop {
            return RustBacktestTrade::close(signal, "stopped", entry_date, entry_p, bar_date, active_stop, holding_days, mae_price, mfe_price, None);
        }
        if hit_target {
            return RustBacktestTrade::close(signal, "target", entry_date, entry_p, bar_date, target_price.unwrap(), holding_days, mae_price, mfe_price, None);
        }

        if params.exit_mode != ExitMode::V1Fixed && risk > 0.0 {
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
                return RustBacktestTrade::close(signal, "dead_money", entry_date, entry_p, bar_date, close, holding_days, mae_price, mfe_price, None);
            }
        }

        if holding_days == params.max_hold_days {
            exit_p = close;
            exit_d = bar_date;
            break;
        }
    }

    RustBacktestTrade::close(signal, "time_stop", entry_date, entry_p, exit_d, exit_p, exit_end - entry_idx, mae_price, mfe_price, None)
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

    fn close(sig: &RustSignal, status: &str, entry_date: NaiveDate, entry_price: f64, exit_date: NaiveDate, exit_price: f64, holding_days: usize, mae: f64, mfe: f64, reason: Option<String>) -> Self {
        let risk = entry_price - sig.stop;
        let return_pct = (exit_price - entry_price) / entry_price * 100.0;
        let r_multiple = if risk > 0.0 { Some((exit_price - entry_price) / risk) } else { None };
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

#[pymodule]
fn multibagger_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(health_check, m)?)?;
    m.add_function(wrap_pyfunction!(run_backtest_from_db_rust, m)?)?;
    m.add_function(wrap_pyfunction!(run_bulk_backtest_rust_v2, m)?)?;
    Ok(())
}
