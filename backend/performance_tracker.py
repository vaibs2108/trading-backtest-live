"""
performance_tracker.py — Equity curve, key metrics, and drift/anomaly detection.

Derives all stats from the trading journal (journal_manager.py).
Provides API-ready data for the Performance tab.
Also logs performance snapshots to CSV for download/analysis.
"""
import csv
import math
import logging
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional

import pytz

logger = logging.getLogger(__name__)
_IST = pytz.timezone("Asia/Kolkata")

_PERF_LOG_PATH = Path(__file__).parent / "data" / "performance_log.csv"
_PERF_CSV_HEADERS = [
    "date", "trade_count", "wins", "losses", "gross_pnl",
    "cumulative_pnl", "equity", "win_rate", "avg_win", "avg_loss",
    "profit_factor", "max_drawdown", "sharpe_approx",
]


def _ensure_csv():
    _PERF_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _PERF_LOG_PATH.exists():
        with open(_PERF_LOG_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(_PERF_CSV_HEADERS)


def get_dhan_closed_positions(target_instrument: str) -> list:
    """Fetch and parse closed positions directly from Dhan positions data."""
    import broker
    if not broker.is_connected():
        return []
    try:
        pos_df = broker.get_positions()
        if pos_df is None or pos_df.empty:
            return []
        
        # Filter by instrument (e.g. tradingSymbol contains BANKNIFTY)
        inst_upper = target_instrument.upper()
        pos_df = pos_df[pos_df['tradingSymbol'].str.upper().str.contains(inst_upper, na=False)]
        if pos_df.empty:
            return []
            
        closed_trades = []
        for idx, row in pos_df.iterrows():
            net_qty = int(row.get('netQty', 0) or 0)
            if net_qty == 0:
                pnl = float(row.get('realizedProfit', 0.0) or row.get('realisedProfit', 0.0) or 0.0)
                buy_qty = int(row.get('buyQty', 0) or row.get('dayBuyQty', 0) or 0)
                sell_qty = int(row.get('sellQty', 0) or row.get('daySellQty', 0) or 0)
                if pnl == 0.0 and buy_qty == 0 and sell_qty == 0:
                    continue
                    
                symbol = row.get('tradingSymbol', '')
                is_option = any(term in symbol.upper() for term in ('-CE', '-PE', ' CALL', ' PUT'))
                
                # Resolve direction
                if is_option:
                    direction = "LONG" if ('-CE' in symbol.upper() or 'CALL' in symbol.upper()) else "SHORT"
                else:
                    direction = "LONG" if buy_qty >= sell_qty else "SHORT"
                    
                buy_avg = float(row.get('buyAvg', 0.0) or 0.0)
                cost_price = float(row.get('costPrice', 0.0) or 0.0)
                
                # Use costPrice as the entry price to match the portal average buy price
                entry_price = cost_price if cost_price > 0 else buy_avg
                exit_price = float(row.get('sellAvg', 0.0) or 0.0)
                if direction == "SHORT":
                    entry_price, exit_price = exit_price, entry_price
                    
                # Adjust realized P&L to match portal cost basis
                if cost_price > 0 and buy_avg > 0 and buy_qty > 0:
                    pnl = pnl - (cost_price - buy_avg) * buy_qty
                    
                qty = max(buy_qty, sell_qty)
                
                closed_trades.append({
                    "trade_id": f"DHAN_POS_{symbol}",
                    "status": "CLOSED",
                    "instrument": target_instrument,
                    "trade_mode": "OPTIONS" if is_option else "INDEX",
                    "symbol": symbol,
                    "direction": direction,
                    "qty": qty,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "entry_date": datetime.now(_IST).strftime("%Y-%m-%d"),
                    "entry_time": "09:15:00",
                    "exit_date": datetime.now(_IST).strftime("%Y-%m-%d"),
                    "exit_time": "15:30:00",
                    "pnl": round(pnl, 2),
                    "exit_reason": "DHAN_CLOSED",
                    "reasons": [f"Dhan closed position: {symbol} with realized P&L Rs.{pnl:,.2f}"]
                })
        return closed_trades
    except Exception as e:
        logger.error(f"get_dhan_closed_positions error: {e}")
        return []


def get_dhan_reconstructed_trades(target_instrument: str, days: int = 30) -> list:
    """
    Fetch raw trades from Dhan (both today's book and history) and reconstruct them into closed/open trades.
    Filters by the target_instrument.
    """
    import broker
    from datetime import datetime, timedelta
    import pytz
    
    if not broker.is_connected():
        return []
        
    try:
        _IST = pytz.timezone("Asia/Kolkata")
        now_ist = datetime.now(_IST)
        from_date = (now_ist - timedelta(days=days)).strftime("%Y-%m-%d")
        to_date = now_ist.strftime("%Y-%m-%d")
        
        hist_trades = broker.get_trade_history(from_date, to_date) if hasattr(broker, "get_trade_history") else []
        today_trades = broker.get_trade_book() if hasattr(broker, "get_trade_book") else []
        
        # Combine and deduplicate
        all_execs = {}
        for idx, t in enumerate(hist_trades + today_trades):
            etid = t.get("exchangeTradeId") or t.get("tradeId")
            tx_type = t.get("transactionType") or t.get("type") or ""
            px = t.get("tradedPrice") or t.get("price") or 0.0
            order_id = t.get("orderId") or ""
            if etid and etid != "0":
                trade_key = etid
            else:
                trade_key = f"{order_id}_{tx_type}_{px}_{idx}"
            all_execs[trade_key] = t
            
        # Reconstruct closed/open trades
        raw_list = list(all_execs.values())
        
        def get_time(t):
            for key in ["createTime", "exchangeTime", "updateTime"]:
                val = t.get(key)
                if val and val != "NA":
                    return val
            return ""
            
        trades_sorted = sorted(raw_list, key=get_time)
        
        from collections import defaultdict
        by_symbol = defaultdict(list)
        for t in trades_sorted:
            sym = t.get("customSymbol") or t.get("tradingSymbol")
            if sym:
                by_symbol[sym].append(t)
                
        reconstructed = []
        
        # Helper to guess instrument from symbol
        def guess_instrument(symbol: str) -> str:
            s = symbol.upper()
            if "BANKNIFTY" in s:
                return "BANKNIFTY"
            if "FINNIFTY" in s:
                return "FINNIFTY"
            if "MIDCPNIFTY" in s:
                return "MIDCPNIFTY"
            if "CRUDEOIL" in s:
                return "CRUDEOIL"
            if "NIFTY" in s:
                return "NIFTY"
            return "INDEX"
            
        for symbol, sym_trades in by_symbol.items():
            active = None
            for t_exec in sym_trades:
                qty = int(t_exec.get("tradedQuantity", 0) or t_exec.get("quantity", 0) or 0)
                if qty <= 0:
                    continue
                price = float(t_exec.get("tradedPrice", 0.0) or t_exec.get("price", 0.0) or 0.0)
                tx_type = (t_exec.get("transactionType") or t_exec.get("type") or "").upper()
                if not tx_type:
                    continue
                    
                time_str = get_time(t_exec)
                if "T" in time_str:
                    parts = time_str.split("T")
                else:
                    parts = time_str.split(" ")
                date_part = parts[0] if len(parts) > 0 else ""
                time_part = parts[1] if len(parts) > 1 else ""
                
                etid = t_exec.get("exchangeTradeId") or t_exec.get("tradeId")
                if etid and etid != "0":
                    trade_id = etid
                else:
                    trade_id = f"DHAN_{t_exec.get('orderId')}_{tx_type}"
                    
                direction = "LONG" if tx_type == "BUY" else "SHORT"
                
                if active is None:
                    active = {
                        "trade_id": trade_id,
                        "status": "OPEN",
                        "instrument": guess_instrument(symbol),
                        "symbol": symbol,
                        "direction": direction,
                        "qty": qty,
                        "entry_date": date_part,
                        "entry_time": time_part,
                        "entry_price": price,
                        "exit_price": None,
                        "exit_time": None,
                        "exit_reason": None,
                        "pnl": None
                    }
                else:
                    if active["direction"] == direction:
                        total_qty = active["qty"] + qty
                        active["entry_price"] = round(((active["entry_price"] * active["qty"]) + (price * qty)) / total_qty, 2)
                        active["qty"] = total_qty
                    else:
                        if qty >= active["qty"]:
                            closed_qty = active["qty"]
                            pnl = round((price - active["entry_price"]) * closed_qty, 2) if active["direction"] == "LONG" else round((active["entry_price"] - price) * closed_qty, 2)
                            active["status"] = "CLOSED"
                            active["exit_price"] = price
                            active["exit_time"] = time_part
                            active["pnl"] = pnl
                            active["exit_reason"] = "DHAN_CLOSED"
                            reconstructed.append(active)
                            
                            rem_qty = qty - closed_qty
                            if rem_qty > 0:
                                active = {
                                    "trade_id": trade_id,
                                    "status": "OPEN",
                                    "instrument": guess_instrument(symbol),
                                    "symbol": symbol,
                                    "direction": direction,
                                    "qty": rem_qty,
                                    "entry_date": date_part,
                                    "entry_time": time_part,
                                    "entry_price": price,
                                    "exit_price": None,
                                    "exit_time": None,
                                    "exit_reason": None,
                                    "pnl": None
                                }
                            else:
                                active = None
                        else:
                            pnl = round((price - active["entry_price"]) * qty, 2) if active["direction"] == "LONG" else round((active["entry_price"] - price) * qty, 2)
                            closed_part = active.copy()
                            closed_part["qty"] = qty
                            closed_part["status"] = "CLOSED"
                            closed_part["exit_price"] = price
                            closed_part["exit_time"] = time_part
                            closed_part["pnl"] = pnl
                            closed_part["exit_reason"] = "DHAN_PARTIAL_CLOSE"
                            reconstructed.append(closed_part)
                            active["qty"] -= qty
                            
            if active is not None:
                reconstructed.append(active)
                
        # Filter by target_instrument
        inst_upper = target_instrument.upper()
        filtered = [
            t for t in reconstructed
            if inst_upper in t.get("symbol", "").upper() or inst_upper in t.get("instrument", "").upper()
        ]
        return filtered
        
    except Exception as e:
        logger.error(f"Error getting reconstructed Dhan trades for performance: {e}")
        return []


def compute_metrics(starting_capital: float = 50000.0) -> dict:
    """
    Compute full performance metrics from the trading journal.
    Returns dict with equity_curve, summary stats, daily breakdown, and drift alerts.
    """
    from journal_manager import load_journal
    from capital_tracker import get_capital_tracker
    import broker
    from config import get_settings

    ct = get_capital_tracker()
    starting_capital = ct.starting_capital

    broker_connected = broker.is_connected()
    actual_current_equity = starting_capital
    if broker_connected:
        try:
            dhan_bal = broker.get_balance()
            if dhan_bal > 0:
                actual_current_equity = dhan_bal
        except Exception:
            pass

    cfg = get_settings()
    target_instrument = cfg.instrument

    if broker_connected:
        # Load actual trades from Dhan (today's book + history)
        dhan_trades = get_dhan_reconstructed_trades(target_instrument, days=30)
        closed = [t for t in dhan_trades if t.get("status") == "CLOSED"]
    else:
        journal = load_journal()
        local_closed = [
            e for e in journal 
            if e.get("status") == "CLOSED" 
            and e.get("pnl") is not None
            and e.get("instrument") == target_instrument
        ]
        closed = local_closed

    if not closed:
        summary = _empty_summary(starting_capital)
        summary["current_equity"] = round(actual_current_equity, 2)
        return {
            "summary": summary,
            "equity_curve": [],
            "daily_breakdown": [],
            "drift_alerts": [],
            "trade_count": 0,
        }

    # Sort by exit time
    closed.sort(key=lambda e: e.get("exit_time") or e.get("entry_timestamp", ""))

    # -- Equity curve (cumulative per trade) --
    equity_curve = []
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    pnls = []
    wins = []
    losses = []
    consec_w = 0
    consec_l = 0
    max_consec_w = 0
    max_consec_l = 0

    for t in closed:
        pnl = float(t.get("pnl", 0))
        pnls.append(pnl)
        cumulative += pnl
        equity = starting_capital + cumulative

        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

        if pnl > 0:
            wins.append(pnl)
            consec_w += 1
            consec_l = 0
            if consec_w > max_consec_w:
                max_consec_w = consec_w
        else:
            losses.append(pnl)
            consec_l += 1
            consec_w = 0
            if consec_l > max_consec_l:
                max_consec_l = consec_l

        equity_curve.append({
            "trade_num": len(equity_curve) + 1,
            "time": t.get("exit_time") or t.get("entry_timestamp", ""),
            "date": t.get("entry_date", ""),
            "pnl": round(pnl, 2),
            "cumulative_pnl": round(cumulative, 2),
            "equity": round(equity, 2),
            "drawdown": round(dd, 2),
            "symbol": t.get("symbol", ""),
            "direction": t.get("direction", ""),
            "exit_reason": t.get("exit_reason", ""),
        })

    # -- Summary stats --
    total = len(pnls)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total * 100) if total > 0 else 0
    avg_win = sum(wins) / win_count if win_count > 0 else 0
    avg_loss = sum(losses) / loss_count if loss_count > 0 else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0
    net_pnl = sum(pnls)

    # Sharpe approximation (annualised, assuming ~250 trading days)
    if len(pnls) >= 2:
        mean_pnl = sum(pnls) / len(pnls)
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std_pnl = math.sqrt(variance) if variance > 0 else 0
        sharpe = (mean_pnl / std_pnl * math.sqrt(250)) if std_pnl > 0 else 0
    else:
        sharpe = 0

    # Expectancy
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss) if total > 0 else 0

    summary = {
        "total_trades": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(net_pnl, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else 999.0,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd / starting_capital * 100, 2) if starting_capital > 0 else 0,
        "sharpe_ratio": round(sharpe, 2),
        "expectancy": round(expectancy, 2),
        "max_consec_wins": max_consec_w,
        "max_consec_losses": max_consec_l,
        "starting_capital": starting_capital,
        "current_equity": round(actual_current_equity, 2),
    }

    # -- Daily breakdown --
    daily = {}
    for t in closed:
        d = t.get("entry_date", "unknown")
        if d not in daily:
            daily[d] = {"date": d, "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        daily[d]["trades"] += 1
        pnl = float(t.get("pnl", 0))
        daily[d]["pnl"] += pnl
        if pnl > 0:
            daily[d]["wins"] += 1
        else:
            daily[d]["losses"] += 1

    daily_breakdown = sorted(daily.values(), key=lambda x: x["date"])
    for d in daily_breakdown:
        d["pnl"] = round(d["pnl"], 2)
        d["win_rate"] = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] > 0 else 0

    # -- Drift alerts (rolling window analysis) --
    drift_alerts = _detect_drift(pnls, win_rate)

    # -- Exit reason breakdown --
    exit_reasons = {}
    for t in closed:
        reason = t.get("exit_reason", "UNKNOWN")
        if reason not in exit_reasons:
            exit_reasons[reason] = {"count": 0, "total_pnl": 0.0, "wins": 0}
        exit_reasons[reason]["count"] += 1
        p = float(t.get("pnl", 0))
        exit_reasons[reason]["total_pnl"] += p
        if p > 0:
            exit_reasons[reason]["wins"] += 1
    for r in exit_reasons.values():
        r["total_pnl"] = round(r["total_pnl"], 2)
        r["win_rate"] = round(r["wins"] / r["count"] * 100, 1) if r["count"] > 0 else 0

    return {
        "summary": summary,
        "equity_curve": equity_curve,
        "daily_breakdown": daily_breakdown,
        "exit_reasons": exit_reasons,
        "drift_alerts": drift_alerts,
        "trade_count": total,
    }


def _empty_summary(starting_capital: float) -> dict:
    return {
        "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
        "avg_win": 0, "avg_loss": 0, "gross_profit": 0, "gross_loss": 0,
        "net_pnl": 0, "profit_factor": 0, "max_drawdown": 0, "max_drawdown_pct": 0,
        "sharpe_ratio": 0, "expectancy": 0, "max_consec_wins": 0, "max_consec_losses": 0,
        "starting_capital": starting_capital, "current_equity": starting_capital,
    }


def _detect_drift(pnls: List[float], overall_win_rate: float) -> List[dict]:
    """
    Detect performance drift by comparing rolling windows to overall stats.
    Returns list of alert dicts.
    """
    alerts = []
    if len(pnls) < 10:
        return alerts

    # Rolling 10-trade window
    window = 10
    recent = pnls[-window:]
    recent_wins = sum(1 for p in recent if p > 0)
    recent_wr = recent_wins / window * 100
    recent_avg = sum(recent) / window

    # Alert if win rate dropped significantly (>20% below overall)
    if overall_win_rate > 0 and recent_wr < overall_win_rate - 20:
        alerts.append({
            "type": "WIN_RATE_DROP",
            "severity": "WARNING",
            "message": f"Win rate dropped to {recent_wr:.0f}% in last {window} trades (overall: {overall_win_rate:.0f}%)",
            "recent_value": round(recent_wr, 1),
            "overall_value": round(overall_win_rate, 1),
            "window": window,
        })

    # Alert if last 5 trades are all losses
    if len(pnls) >= 5 and all(p <= 0 for p in pnls[-5:]):
        alerts.append({
            "type": "LOSING_STREAK",
            "severity": "CRITICAL",
            "message": "5 consecutive losing trades detected",
            "recent_value": 5,
        })

    # Alert if recent avg PnL is significantly negative
    overall_avg = sum(pnls) / len(pnls) if pnls else 0
    if overall_avg > 0 and recent_avg < 0:
        alerts.append({
            "type": "AVG_PNL_NEGATIVE",
            "severity": "WARNING",
            "message": f"Recent {window}-trade avg PnL is Rs.{recent_avg:,.0f} (overall avg: Rs.{overall_avg:,.0f})",
            "recent_value": round(recent_avg, 2),
            "overall_value": round(overall_avg, 2),
            "window": window,
        })

    return alerts


def log_daily_snapshot(starting_capital: float = 50000.0):
    """
    Append today's performance summary to the CSV log.
    Called at EOD or on demand.
    """
    _ensure_csv()
    metrics = compute_metrics(starting_capital)
    s = metrics["summary"]
    today_str = date.today().isoformat()

    # Check if already logged today
    try:
        with open(_PERF_LOG_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("date") == today_str:
                    return  # already logged
    except Exception:
        pass

    row = [
        today_str,
        s["total_trades"], s["wins"], s["losses"],
        s["net_pnl"], s["net_pnl"], s["current_equity"],
        s["win_rate"], s["avg_win"], s["avg_loss"],
        s["profit_factor"], s["max_drawdown"], s["sharpe_ratio"],
    ]
    try:
        with open(_PERF_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)
        logger.info(f"Performance snapshot logged for {today_str}")
    except Exception as e:
        logger.warning(f"Failed to log performance snapshot: {e}")
