"""
performance.py
--------------
Computes performance dashboard metrics from a DataFrame of trades
(shape matching the Supabase `trades` table). Pure functions, no I/O --
takes a DataFrame in, returns metrics out, so it's independently
testable without a live database.
"""

import pandas as pd
import numpy as np


REQUIRED_COLS = ["trade_date", "result", "pl_amount"]


def _ensure_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["pl_amount"] = pd.to_numeric(df["pl_amount"], errors="coerce").fillna(0.0)
    if "risk_reward" in df.columns:
        df["risk_reward"] = pd.to_numeric(df["risk_reward"], errors="coerce")
    return df


def daily_pl(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregates trades by day: net P/L, trade count, win/loss/breakeven counts."""
    if df.empty:
        return pd.DataFrame(columns=["trade_date", "net_pl", "n_trades", "wins", "losses", "breakevens"])
    df = _ensure_types(df)
    grouped = df.groupby(df["trade_date"].dt.date).agg(
        net_pl=("pl_amount", "sum"),
        n_trades=("pl_amount", "count"),
        wins=("result", lambda s: (s == "win").sum()),
        losses=("result", lambda s: (s == "loss").sum()),
        breakevens=("result", lambda s: (s == "breakeven").sum()),
    ).reset_index()
    grouped.columns = ["trade_date", "net_pl", "n_trades", "wins", "losses", "breakevens"]
    return grouped


def compute_dashboard(df: pd.DataFrame) -> dict:
    """Returns the full set of performance dashboard metrics."""
    if df.empty:
        return {"empty": True}

    df = _ensure_types(df)

    total_trades = len(df)
    wins = df[df["result"] == "win"]
    losses = df[df["result"] == "loss"]
    breakevens = df[df["result"] == "breakeven"]

    n_wins, n_losses, n_be = len(wins), len(losses), len(breakevens)
    win_rate = n_wins / total_trades if total_trades else 0.0

    total_profit = wins["pl_amount"].sum() if n_wins else 0.0
    total_losses = losses["pl_amount"].sum() if n_losses else 0.0  # expected negative
    net_pl = df["pl_amount"].sum()

    avg_win = wins["pl_amount"].mean() if n_wins else 0.0
    avg_loss = losses["pl_amount"].mean() if n_losses else 0.0

    gross_profit = wins["pl_amount"].sum() if n_wins else 0.0
    gross_loss = abs(losses["pl_amount"].sum()) if n_losses else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (np.inf if gross_profit > 0 else 0.0)

    avg_rr = df["risk_reward"].mean() if "risk_reward" in df.columns and df["risk_reward"].notna().any() else None

    daily = daily_pl(df)
    best_day = daily.loc[daily["net_pl"].idxmax()] if not daily.empty else None
    worst_day = daily.loc[daily["net_pl"].idxmin()] if not daily.empty else None

    # Streaks: based on per-trade result sequence, ordered by date then created_at if present
    sort_cols = ["trade_date"] + (["created_at"] if "created_at" in df.columns else [])
    ordered = df.sort_values(sort_cols)
    results_seq = ordered["result"].tolist()

    def current_streak(seq, target):
        streak = 0
        for r in reversed(seq):
            if r == target:
                streak += 1
            elif r in ("win", "loss"):  # breakeven doesn't break a streak count but doesn't extend it either
                break
        return streak

    current_win_streak = current_streak(results_seq, "win")
    current_loss_streak = current_streak(results_seq, "loss")

    df["week"] = df["trade_date"].dt.to_period("W").astype(str)
    df["month"] = df["trade_date"].dt.to_period("M").astype(str)
    weekly = df.groupby("week")["pl_amount"].sum().reset_index().rename(columns={"pl_amount": "net_pl"})
    monthly = df.groupby("month")["pl_amount"].sum().reset_index().rename(columns={"pl_amount": "net_pl"})

    return {
        "empty": False,
        "TotalTrades": total_trades,
        "WinningTrades": n_wins,
        "LosingTrades": n_losses,
        "BreakevenTrades": n_be,
        "WinRate": round(win_rate * 100, 1),
        "TotalProfit": round(float(total_profit), 2),
        "TotalLosses": round(float(total_losses), 2),
        "NetPL": round(float(net_pl), 2),
        "AvgWin": round(float(avg_win), 2),
        "AvgLoss": round(float(avg_loss), 2),
        "ProfitFactor": round(float(profit_factor), 2) if np.isfinite(profit_factor) else None,
        "AvgRiskReward": round(float(avg_rr), 2) if avg_rr is not None and not np.isnan(avg_rr) else None,
        "BestDay": {"date": str(best_day["trade_date"]), "net_pl": round(float(best_day["net_pl"]), 2)} if best_day is not None else None,
        "WorstDay": {"date": str(worst_day["trade_date"]), "net_pl": round(float(worst_day["net_pl"]), 2)} if worst_day is not None else None,
        "CurrentWinStreak": current_win_streak,
        "CurrentLossStreak": current_loss_streak,
        "WeeklyPL": weekly,
        "MonthlyPL": monthly,
        "DailyPL": daily,
    }
