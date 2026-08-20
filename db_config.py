"""
db_config.py
------------
Reads Supabase credentials from Streamlit secrets (the secure way --
set via Streamlit Cloud's App settings > Secrets, never committed to
GitHub) with a sidebar-input fallback for local testing.
"""

import streamlit as st
from journal_db import JournalDB


def get_journal_db():
    """
    Returns (JournalDB instance or None, status message).
    Prefers st.secrets (["supabase"]["url"] / ["supabase"]["key"]);
    falls back to sidebar text inputs if secrets aren't configured.
    """
    url, key = None, None

    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
    except Exception:
        pass

    if not url or not key:
        with st.sidebar:
            st.divider()
            st.header("5. Trade journal (Supabase)")
            st.caption("Not found in secrets -- enter manually for this session, "
                       "or set up secrets permanently (see README).")
            url = st.text_input("Supabase Project URL", value=url or "")
            key = st.text_input("Supabase Publishable/anon key", value=key or "", type="password")

    if not url or not key:
        return None, "Not configured -- enter your Supabase URL and key in the sidebar, or set up secrets."

    db = JournalDB(url, key)
    ok, msg = db.test_connection()
    return (db if ok else None), msg
