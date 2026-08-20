"""
journal_db.py
-------------
Thin wrapper around Supabase's REST API (PostgREST) for the `trades`
table. Uses plain `requests` rather than the supabase-py SDK to keep
dependencies minimal -- Supabase's REST API is stable and simple enough
that a dedicated SDK isn't necessary for this use case.

Needs two values (never hardcoded here -- passed in from Streamlit
secrets): your project URL and publishable (or legacy anon) API key.
"""

import requests

TABLE = "trades"


class JournalDB:
    def __init__(self, project_url: str, api_key: str):
        self.base = project_url.rstrip("/") + f"/rest/v1/{TABLE}"
        self.headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def insert_trade(self, trade: dict) -> dict:
        r = requests.post(self.base, headers=self.headers, json=[trade], timeout=15)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else {}

    def update_trade(self, trade_id: str, updates: dict) -> dict:
        r = requests.patch(f"{self.base}?id=eq.{trade_id}", headers=self.headers, json=updates, timeout=15)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else {}

    def delete_trade(self, trade_id: str) -> bool:
        r = requests.delete(f"{self.base}?id=eq.{trade_id}", headers=self.headers, timeout=15)
        r.raise_for_status()
        return True

    def get_trades(self, date_from: str = None, date_to: str = None, limit: int = 1000) -> list:
        """date_from/date_to: 'YYYY-MM-DD' strings, inclusive."""
        params = [f"order=trade_date.desc", f"limit={limit}"]
        if date_from:
            params.append(f"trade_date=gte.{date_from}")
        if date_to:
            params.append(f"trade_date=lte.{date_to}")
        url = f"{self.base}?{'&'.join(params)}"
        r = requests.get(url, headers=self.headers, timeout=15)
        r.raise_for_status()
        return r.json()

    def test_connection(self) -> tuple:
        """Returns (ok: bool, message: str) -- used to show a clear status in the sidebar."""
        try:
            r = requests.get(f"{self.base}?limit=1", headers=self.headers, timeout=10)
            if r.status_code == 200:
                return True, "Connected"
            return False, f"Connection failed (HTTP {r.status_code}): {r.text[:200]}"
        except Exception as e:
            return False, f"Connection failed: {e}"
