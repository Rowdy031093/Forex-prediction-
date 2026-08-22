"""
instruments.py
--------------
Ticker definitions for indices, metals, and crypto -- asset classes that
don't fit the currency-fundamental-strength model (they don't have
interest rates, CPI, GDP the way a currency does). These get
technical-only screening instead of the fundamental x technical
cross-reference used for forex pairs.

Tickers are yfinance-compatible. Edit these dicts to add/remove
instruments -- display name -> yfinance ticker.
"""

INDICES = {
    "S&P 500": "^GSPC",
    "Nasdaq 100": "^NDX",
    "Dow Jones": "^DJI",
    "FTSE 100": "^FTSE",
    "DAX": "^GDAXI",
    "Nikkei 225": "^N225",
}

METALS = {
    "Gold": "XAUUSD=X",
    "Silver": "XAGUSD=X",
    "Copper": "HG=F",
    "Platinum": "PL=F",
}

ENERGY = {
    "Crude Oil (WTI)": "CL=F",
    "Brent Crude": "BZ=F",
    "Natural Gas": "NG=F",
}

CRYPTO = {
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
    "Solana": "SOL-USD",
    "XRP": "XRP-USD",
}

INSTRUMENT_GROUPS = {
    "Indices": INDICES,
    "Metals": METALS,
    "Energy": ENERGY,
    "Crypto": CRYPTO,
}


def all_instruments() -> dict:
    """Flat dict of display name -> ticker across all groups."""
    out = {}
    for group in INSTRUMENT_GROUPS.values():
        out.update(group)
    return out


def group_of(display_name: str) -> str:
    for group_name, members in INSTRUMENT_GROUPS.items():
        if display_name in members:
            return group_name
    return "Unknown"
