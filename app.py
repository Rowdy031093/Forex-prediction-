"""
app.py
------
Browser-based app for the forex fundamental x technical system, plus
trade journal / calendar / performance dashboard.
Launch with:  streamlit run app.py
(or just double-click run_app.command / run_app.bat)

This is a UI wrapper only -- all the actual scoring logic lives in
fundamental.py, technical.py, data_feed.py, fred_fetch.py, daily_analysis.py,
performance.py, journal_db.py, unchanged from the underlying modules, so
nothing about how numbers are computed is different here.
"""

import os
import calendar as pycal
import tempfile
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from fundamental import load_fundamental_data, compute_currency_strength, WEIGHTS
from technical import compute_technical_signals
from data_feed import get_ohlc, generate_synthetic_ohlc
from engine import default_pairs
from daily_analysis import build_daily_analysis
from news_events import fetch_upcoming_events
from db_config import get_journal_db
from performance import compute_dashboard, daily_pl
import fred_fetch

st.set_page_config(page_title="Forex Fundamental x Technical", layout="wide")

TEMPLATE_CSV = os.path.join(os.path.dirname(__file__), "fundamental_data_template.csv")
TEMPLATE_COLUMNS = fred_fetch.TEMPLATE_COLUMNS

if "fundamentals_df" not in st.session_state:
    st.session_state.fundamentals_df = pd.read_csv(TEMPLATE_CSV).set_index("Currency")
if "results" not in st.session_state:
    st.session_state.results = None
if "cal_month" not in st.session_state:
    today = date.today()
    st.session_state.cal_month = (today.year, today.month)
if "cal_selected_day" not in st.session_state:
    st.session_state.cal_selected_day = None

st.title("Forex: Fundamental x Technical Cross-Reference")
st.caption("Screening tool for idea generation -- not a signal advice. "
           "See the README for the full list of limitations.")

# ------------------------------------------------------------------
# SIDEBAR: data sources & settings (applies across all tabs)
# ------------------------------------------------------------------
with st.sidebar:
    st.header("1. Fundamental data")
    source = st.radio("Source", ["FRED (auto-fetch)", "Upload CSV", "Edit in-app"], index=2)

    if source == "FRED (auto-fetch)":
        fred_key = st.text_input("FRED API key", type="password",
                                  value=os.environ.get("FRED_API_KEY", ""),
                                  help="Free key: https://fredapi.stlouisfed.org/docs/api/api_key.html")
        default_currencies = list(fred_fetch.CURRENCY_TO_COUNTRY.keys())
        pick_currencies = st.multiselect("Currencies", default_currencies, default=default_currencies)
        if st.button("Fetch from FRED", type="primary", disabled=not fred_key):
            with st.spinner(f"Fetching {len(pick_currencies)} currencies from FRED..."):
                try:
                    fetched = fred_fetch.build_fundamental_dataframe(fred_key, pick_currencies)
                    st.session_state.fundamentals_df = fetched
                    st.success("Fetched. Review/edit below before running the analysis.")
                except Exception as e:
                    st.error(f"Fetch failed: {e}")

    elif source == "Upload CSV":
        uploaded = st.file_uploader("Fundamental data CSV", type="csv")
        if uploaded is not None:
            try:
                st.session_state.fundamentals_df = pd.read_csv(uploaded).set_index("Currency")
                st.success("Loaded.")
            except Exception as e:
                st.error(f"Could not read CSV: {e}")

    st.divider()
    st.header("2. Price data")
    use_synthetic = st.toggle("Offline demo mode (synthetic prices, no internet needed)", value=False)
    period = st.selectbox("Lookback period", ["3mo", "6mo", "1y", "2y"], index=1)
    interval = st.selectbox("Interval", ["1d", "1h"], index=0)

    st.divider()
    st.header("3. Pairs")
    currencies_available = list(st.session_state.fundamentals_df.index)
    auto_pairs = default_pairs(currencies_available)
    pairs_text = st.text_area("Pairs (comma-separated)", value=",".join(auto_pairs), height=80)

    st.divider()
    st.header("4. Daily analysis settings")
    COMMON_TIMEZONES = ["UTC", "America/New_York", "America/Chicago", "America/Los_Angeles",
                         "Europe/London", "Europe/Berlin", "Asia/Tokyo", "Asia/Singapore",
                         "Australia/Sydney", "Pacific/Auckland"]
    tz_name = st.selectbox("Your timezone (for session hours)", COMMON_TIMEZONES, index=0)
    finnhub_key = st.text_input("Finnhub API key (optional -- for news events)", type="password",
                                 value=os.environ.get("FINNHUB_API_KEY", ""),
                                 help="Free key at finnhub.io. Economic calendar access varies by plan; "
                                      "if unavailable, news events are simply left blank.")

    journal_db, journal_status = get_journal_db()

# ------------------------------------------------------------------
# TOP-LEVEL NAVIGATION
# ------------------------------------------------------------------
nav_screening, nav_journal, nav_calendar, nav_dashboard = st.tabs(
    ["Market Screening", "Trade Journal", "Calendar", "Performance Dashboard"]
)

# ==================================================================
# TAB 1: MARKET SCREENING (all original + Group A functionality)
# ==================================================================
with nav_screening:
    st.subheader("Fundamental data (editable)")
    st.caption("Tweak any value directly in the table -- the analysis below uses whatever is in here right now.")

    # Key includes the table's shape so the browser's data-grid widget fully
    # remounts whenever rows/columns change (e.g. switching source, or a
    # dynamic row add/delete) instead of reusing stale internal grid state --
    # reusing stale state across a shape change is what causes the grid's
    # frontend to crash with a "sticky"/column-index error.
    _fdf = st.session_state.fundamentals_df
    _editor_key = f"fundamentals_editor_{_fdf.shape[0]}x{_fdf.shape[1]}_{'_'.join(_fdf.columns)}"
    edited = st.data_editor(
        _fdf.reset_index(),
        num_rows="dynamic",
        use_container_width=True,
        key=_editor_key,
    )
    st.session_state.fundamentals_df = edited.set_index("Currency")

    with st.expander("Metric weights (fundamental.py WEIGHTS)"):
        st.write(pd.Series(WEIGHTS, name="weight").to_frame())
        st.caption("To change these, edit the WEIGHTS dict at the top of fundamental.py and restart the app.")

    st.divider()

    if st.button("Run analysis", type="primary"):
        df = st.session_state.fundamentals_df.copy()
        pairs = [p.strip().upper() for p in pairs_text.split(",") if p.strip()]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
            df.reset_index().to_csv(tmp.name, index=False)
            tmp_path = tmp.name

        try:
            raw = load_fundamental_data(tmp_path)
            strength = compute_currency_strength(raw)
            max_abs = strength["CompositeScore"].abs().max() or 1.0
            norm_strength = strength["CompositeScore"] / max_abs

            rows = []
            daily_reports = {}
            progress = st.progress(0.0, text="Evaluating pairs...")
            for i, pair in enumerate(pairs):
                base, quote = pair[:3], pair[3:]
                if base not in norm_strength.index or quote not in norm_strength.index:
                    st.warning(f"Skipping {pair}: missing fundamental data for {base} or {quote}")
                    continue
                fundamental_bias = norm_strength[base] - norm_strength[quote]

                try:
                    if use_synthetic:
                        seed = abs(hash(pair)) % (2**32)
                        ohlc = generate_synthetic_ohlc(n=220, seed=seed)
                        hourly_ohlc = generate_synthetic_ohlc(n=300, seed=seed + 1, freq="h")
                    else:
                        ohlc = get_ohlc(pair, period=period, interval=interval)
                        hourly_ohlc = get_ohlc(pair, period="10d", interval="1h")
                    tech = compute_technical_signals(ohlc)
                except Exception as e:
                    st.warning(f"Skipping {pair}: price data error ({e})")
                    continue

                technical_score = tech["TechnicalScore"]
                agree = (fundamental_bias > 0) == (technical_score > 0) and fundamental_bias != 0
                conviction = abs(fundamental_bias) * abs(technical_score)
                if not agree:
                    conviction *= 0.3

                rows.append({
                    "Pair": pair, "Base": base, "Quote": quote,
                    "FundamentalBias": round(float(fundamental_bias), 3),
                    "TechnicalScore": technical_score,
                    "Structure": tech["Structure"],
                    "Agreement": bool(agree),
                    "ConvictionScore": round(float(conviction), 3),
                    "RSI14": tech["RSI14"],
                })

                try:
                    daily_reports[pair] = build_daily_analysis(
                        pair, fundamental_bias=fundamental_bias,
                        daily_ohlc=ohlc, hourly_ohlc=hourly_ohlc, tz_name=tz_name,
                    )
                except Exception as e:
                    st.warning(f"Daily analysis skipped for {pair}: {e}")

                progress.progress((i + 1) / max(len(pairs), 1), text=f"Evaluated {pair}")

            progress.empty()

            currencies_in_pairs = sorted(set(p[:3] for p in pairs) | set(p[3:] for p in pairs))
            news = fetch_upcoming_events(finnhub_key, currencies_in_pairs) if finnhub_key else []

            st.session_state.results = {
                "strength": strength,
                "pairs": pd.DataFrame(rows).sort_values("ConvictionScore", ascending=False).reset_index(drop=True)
                if rows else pd.DataFrame(),
                "daily_reports": daily_reports,
                "news": news,
            }
        finally:
            os.unlink(tmp_path)

    if st.session_state.results is not None:
        strength = st.session_state.results["strength"]
        pairs_df = st.session_state.results["pairs"]
        daily_reports = st.session_state.results.get("daily_reports", {})
        news = st.session_state.results.get("news", [])

        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
            "Currency Strength", "Pair Cross-Reference", "Clearest Structure",
            "Daily Market Analysis", "Session Times", "Setup Scores",
        ])

        with tab1:
            st.bar_chart(strength["CompositeScore"])
            st.dataframe(strength[["CompositeScore", "Rank"]], use_container_width=True)
            st.download_button("Download strength ranking (CSV)",
                                strength.to_csv().encode(), "currency_strength.csv")

        with tab2:
            if pairs_df.empty:
                st.info("No pairs were evaluated -- check the Pairs list and fundamental data.")
            else:
                st.dataframe(pairs_df, use_container_width=True)
                st.download_button("Download pair results (CSV)",
                                    pairs_df.to_csv(index=False).encode(), "pair_cross_reference.csv")

        with tab3:
            if pairs_df.empty:
                st.info("No pairs were evaluated.")
            else:
                top = pairs_df[pairs_df["Agreement"]].head(5)
                if top.empty:
                    st.info("No pairs currently show fundamental/technical agreement.")
                for _, r in top.iterrows():
                    direction = "Bullish" if r["FundamentalBias"] > 0 else "Bearish"
                    st.metric(
                        label=f"{r['Pair']} -- {direction} {r['Base']} vs {r['Quote']}",
                        value=f"Conviction {r['ConvictionScore']:.2f}",
                        delta=f"structure: {r['Structure']}",
                    )

        with tab4:
            st.caption("Informational analysis only -- not a trade recommendation and does not guarantee results.")
            if not daily_reports:
                st.info("No daily analysis available -- run the analysis above first.")
            else:
                pick = st.selectbox("Pair", list(daily_reports.keys()), key="daily_analysis_pick")
                r = daily_reports[pick]

                c1, c2, c3 = st.columns(3)
                c1.metric("Direction Bias", r["DirectionBias"])
                c2.metric("Alignment", r["Alignment"]["Alignment"])
                c3.metric("Setup Grade", r["SetupGrade"]["Grade"])

                if r["Alignment"]["Warning"]:
                    st.warning(r["Alignment"]["Warning"])

                colA, colB = st.columns(2)
                with colA:
                    st.markdown("**Technical Analysis**")
                    st.write(r["TechnicalAnalysis"])
                    st.markdown("**1H Market Structure**")
                    st.write(r["MarketStructure1H"])
                with colB:
                    st.markdown("**Fundamental Analysis**")
                    st.write(r["FundamentalAnalysis"])
                    st.markdown("**Support / Resistance**")
                    st.write(r["SupportResistance"])

                st.markdown("**Scenarios**")
                st.write(f"Bullish: {r['Scenarios']['bullish_scenario']}")
                st.write(f"Bearish: {r['Scenarios']['bearish_scenario']}")

                st.markdown("**Key Levels to Watch**")
                st.write(r["KeyLevelsToWatch"])

                st.markdown("**Overall Trade Bias**")
                st.info(r["OverallTradeBias"])

                if r["NewsEvents"]:
                    st.markdown("**Upcoming Economic Events**")
                    st.dataframe(pd.DataFrame(r["NewsEvents"]), use_container_width=True)
                else:
                    st.caption("No automated news events available (add a Finnhub key in the sidebar, "
                               "or note events manually) -- add manually if relevant for this pair.")

                st.caption(r["Disclaimer"])

        with tab5:
            if not daily_reports:
                st.info("No session data available -- run the analysis above first.")
            else:
                session_rows = []
                for pair, r in daily_reports.items():
                    s = r["Session"]
                    session_rows.append({
                        "Pair": pair,
                        "Primary Session(s)": ", ".join(s["primary_sessions"]),
                        "Active Now": ", ".join(s["active_sessions_now"]) or "None",
                        "Overlap Now": ", ".join(s["active_overlaps_now"]) or "None",
                        "Status": s["current_status"],
                        "Best Hours (local)": s.get("best_hours_local", s.get("best_hours_utc", "n/a")),
                        "Quietest Hours (local)": s.get("quietest_hours_local", s.get("quietest_hours_utc", "n/a")),
                    })
                st.dataframe(pd.DataFrame(session_rows), use_container_width=True)
                st.caption("Best/quietest hours are computed from actual recent hourly price moves for each pair, "
                           "not fixed assumptions. London/New York shift 1 hour during their daylight saving periods.")

        with tab6:
            if not daily_reports:
                st.info("No setup scores available -- run the analysis above first.")
            else:
                grade_rows = []
                for pair, r in daily_reports.items():
                    grade_rows.append({
                        "Pair": pair, "Grade": r["SetupGrade"]["Grade"],
                        "Points": f"{r['SetupGrade']['Points']}/{r['SetupGrade']['MaxPoints']}",
                        "Alignment": r["Alignment"]["Alignment"],
                    })
                grade_df = pd.DataFrame(grade_rows).sort_values("Points", ascending=False)
                st.dataframe(grade_df, use_container_width=True)

                pick2 = st.selectbox("See why a pair got its grade", list(daily_reports.keys()), key="grade_explain_pick")
                st.markdown(f"**{pick2}: {daily_reports[pick2]['SetupGrade']['Grade']}**")
                for line in daily_reports[pick2]["SetupGrade"]["Breakdown"]:
                    st.write(f"- {line}")
    else:
        st.info("Review the fundamental table above, then click **Run analysis**.")

# ==================================================================
# TAB 2: TRADE JOURNAL
# ==================================================================
with nav_journal:
    if journal_db is None:
        st.warning(f"Trade journal not connected: {journal_status}")
        st.caption("Enter your Supabase URL and key in the sidebar (section 5), or set them up as "
                   "permanent secrets -- see the README.")
    else:
        st.success(f"Connected to trade journal database ({journal_status})")

        with st.expander("Log a new trade", expanded=True):
            with st.form("new_trade_form", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                trade_date = c1.date_input("Trade date", value=date.today())
                pair = c2.text_input("Pair", value="EURUSD").upper()
                direction = c3.selectbox("Direction", ["Long", "Short"])

                c4, c5, c6 = st.columns(3)
                entry_price = c4.number_input("Entry price", value=0.0, format="%.5f")
                stop_loss = c5.number_input("Stop loss", value=0.0, format="%.5f")
                take_profit = c6.number_input("Take profit", value=0.0, format="%.5f")

                c7, c8, c9 = st.columns(3)
                position_size = c7.number_input("Position size", value=0.0)
                risk_percent = c8.number_input("Risk %", value=1.0)
                result = c9.selectbox("Result", ["win", "loss", "breakeven"])

                c10, c11 = st.columns(2)
                pl_amount = c10.number_input("P/L amount", value=0.0)
                if entry_price and stop_loss and take_profit and entry_price != stop_loss:
                    auto_rr = abs(take_profit - entry_price) / abs(entry_price - stop_loss)
                else:
                    auto_rr = 0.0
                risk_reward = c11.number_input("Risk:Reward", value=round(auto_rr, 2),
                                                help="Auto-calculated from entry/SL/TP -- adjust if needed")

                reason_for_entry = st.text_area("Reason for entry")
                technical_setup = st.text_area("Technical setup")
                fundamental_reasoning = st.text_area("Fundamental reasoning")
                c12, c13 = st.columns(2)
                structure_1h = c12.selectbox("1H structure at entry", ["uptrend", "downtrend", "range", "broken"])
                aligned = c13.checkbox("Technical + fundamental + 1H structure were aligned")

                screenshot_url = st.text_input("Screenshot URL (optional -- paste a link)")
                notes = st.text_area("Post-trade notes")

                submitted = st.form_submit_button("Save trade", type="primary")
                if submitted:
                    try:
                        journal_db.insert_trade({
                            "trade_date": trade_date.isoformat(),
                            "pair": pair,
                            "direction": direction,
                            "entry_price": entry_price or None,
                            "stop_loss": stop_loss or None,
                            "take_profit": take_profit or None,
                            "position_size": position_size or None,
                            "risk_percent": risk_percent or None,
                            "result": result,
                            "pl_amount": pl_amount,
                            "risk_reward": risk_reward or None,
                            "reason_for_entry": reason_for_entry or None,
                            "technical_setup": technical_setup or None,
                            "fundamental_reasoning": fundamental_reasoning or None,
                            "structure_1h_at_entry": structure_1h,
                            "aligned": aligned,
                            "screenshot_url": screenshot_url or None,
                            "notes": notes or None,
                        })
                        st.success("Trade saved.")
                    except Exception as e:
                        st.error(f"Could not save trade: {e}")

        st.divider()
        st.subheader("Recent trades")
        try:
            recent = journal_db.get_trades(limit=100)
            if not recent:
                st.info("No trades logged yet.")
            else:
                recent_df = pd.DataFrame(recent)
                display_cols = [c for c in ["trade_date", "pair", "direction", "result", "pl_amount",
                                             "risk_reward", "entry_price", "stop_loss", "take_profit"]
                                 if c in recent_df.columns]
                st.dataframe(recent_df[display_cols], use_container_width=True)

                del_id = st.selectbox("Delete a trade (select by id)",
                                       [""] + [r["id"] for r in recent],
                                       format_func=lambda x: "-- select --" if x == "" else
                                       f"{x[:8]}... ({next((r['pair'] for r in recent if r['id']==x), '')})")
                if del_id and st.button("Delete selected trade"):
                    journal_db.delete_trade(del_id)
                    st.success("Deleted. Refresh to update the list.")
                    st.rerun()
        except Exception as e:
            st.error(f"Could not load trades: {e}")

# ==================================================================
# TAB 3: CALENDAR
# ==================================================================
with nav_calendar:
    if journal_db is None:
        st.warning(f"Trade journal not connected: {journal_status}")
    else:
        year, month = st.session_state.cal_month
        c1, c2, c3 = st.columns([1, 2, 1])
        if c1.button("< Prev month"):
            month -= 1
            if month == 0:
                month, year = 12, year - 1
            st.session_state.cal_month = (year, month)
            st.session_state.cal_selected_day = None
            st.rerun()
        c2.markdown(f"### {pycal.month_name[month]} {year}")
        if c3.button("Next month >"):
            month += 1
            if month == 13:
                month, year = 1, year + 1
            st.session_state.cal_month = (year, month)
            st.session_state.cal_selected_day = None
            st.rerun()

        first_day = date(year, month, 1)
        last_day = date(year, month, pycal.monthrange(year, month)[1])

        try:
            trades = journal_db.get_trades(date_from=first_day.isoformat(), date_to=last_day.isoformat(), limit=1000)
        except Exception as e:
            st.error(f"Could not load trades: {e}")
            trades = []

        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame(columns=["trade_date", "result", "pl_amount"])
        daily = daily_pl(trades_df) if not trades_df.empty else pd.DataFrame(
            columns=["trade_date", "net_pl", "n_trades", "wins", "losses", "breakevens"])
        daily_lookup = {pd.Timestamp(row["trade_date"]).date(): row for _, row in daily.iterrows()}

        cal_matrix = pycal.Calendar(firstweekday=6).monthdayscalendar(year, month)  # Sunday start
        weekday_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        header_cols = st.columns(7)
        for i, name in enumerate(weekday_names):
            header_cols[i].markdown(f"**{name}**")

        for week in cal_matrix:
            cols = st.columns(7)
            for i, day_num in enumerate(week):
                with cols[i]:
                    if day_num == 0:
                        st.write("")
                        continue
                    d = date(year, month, day_num)
                    row = daily_lookup.get(d)
                    if row is not None:
                        net = row["net_pl"]
                        color = "#1a7f37" if net > 0 else ("#cf222e" if net < 0 else "#6e7781")
                        label = f"{net:+.0f}"
                        st.markdown(
                            f"<div style='border:1px solid {color}; border-radius:6px; padding:4px; "
                            f"text-align:center; background-color:{color}22;'>"
                            f"<b>{day_num}</b><br><span style='color:{color}'>{label}</span>"
                            f"<br><small>{int(row['n_trades'])} trade(s)</small></div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f"<div style='border:1px solid #d0d7de; border-radius:6px; padding:4px; "
                            f"text-align:center; color:#8c959f;'>{day_num}</div>",
                            unsafe_allow_html=True,
                        )
                    if st.button("view", key=f"cal_{d.isoformat()}", use_container_width=True):
                        st.session_state.cal_selected_day = d.isoformat()

        st.divider()
        if st.session_state.cal_selected_day:
            sel = st.session_state.cal_selected_day
            st.markdown(f"**Trades on {sel}**")
            day_trades = [t for t in trades if str(t.get("trade_date")) == sel]
            if day_trades:
                st.dataframe(pd.DataFrame(day_trades), use_container_width=True)
            else:
                st.caption("No trades on this day.")
        else:
            st.caption("Tap 'view' under any day to see that day's trades.")

# ==================================================================
# TAB 4: PERFORMANCE DASHBOARD
# ==================================================================
with nav_dashboard:
    if journal_db is None:
        st.warning(f"Trade journal not connected: {journal_status}")
    else:
        colf1, colf2 = st.columns(2)
        default_start = date.today() - timedelta(days=90)
        start_date = colf1.date_input("From", value=default_start, key="dash_start")
        end_date = colf2.date_input("To", value=date.today(), key="dash_end")

        try:
            trades = journal_db.get_trades(date_from=start_date.isoformat(), date_to=end_date.isoformat(), limit=5000)
        except Exception as e:
            st.error(f"Could not load trades: {e}")
            trades = []

        if not trades:
            st.info("No trades in this date range yet.")
        else:
            df = pd.DataFrame(trades)
            stats = compute_dashboard(df)

            r1 = st.columns(4)
            r1[0].metric("Total Trades", stats["TotalTrades"])
            r1[1].metric("Win Rate", f"{stats['WinRate']}%")
            r1[2].metric("Net P/L", f"{stats['NetPL']:+.2f}")
            r1[3].metric("Profit Factor", stats["ProfitFactor"] if stats["ProfitFactor"] is not None else "\u221e")

            r2 = st.columns(4)
            r2[0].metric("Winning Trades", stats["WinningTrades"])
            r2[1].metric("Losing Trades", stats["LosingTrades"])
            r2[2].metric("Avg Win", f"{stats['AvgWin']:+.2f}")
            r2[3].metric("Avg Loss", f"{stats['AvgLoss']:+.2f}")

            r3 = st.columns(4)
            r3[0].metric("Avg Risk:Reward", stats["AvgRiskReward"] if stats["AvgRiskReward"] is not None else "n/a")
            r3[1].metric("Current Win Streak", stats["CurrentWinStreak"])
            r3[2].metric("Current Loss Streak", stats["CurrentLossStreak"])
            r3[3].metric("Breakeven Trades", stats["BreakevenTrades"])

            if stats["BestDay"]:
                st.caption(f"Best day: {stats['BestDay']['date']} ({stats['BestDay']['net_pl']:+.2f}) | "
                           f"Worst day: {stats['WorstDay']['date']} ({stats['WorstDay']['net_pl']:+.2f})")

            st.divider()
            st.markdown("**Daily P/L**")
            daily = stats["DailyPL"].set_index("trade_date")["net_pl"]
            st.bar_chart(daily)

            st.markdown("**Cumulative P/L**")
            cum = stats["DailyPL"].set_index("trade_date")["net_pl"].cumsum()
            st.line_chart(cum)

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Weekly P/L**")
                st.bar_chart(stats["WeeklyPL"].set_index("week")["net_pl"])
            with c2:
                st.markdown("**Monthly P/L**")
                st.bar_chart(stats["MonthlyPL"].set_index("month")["net_pl"])

            st.download_button("Download trades (CSV)", df.to_csv(index=False).encode(), "trades_export.csv")
