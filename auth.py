"""
GEO Radar — Supabase Auth helpers.

If SUPABASE_URL / SUPABASE_KEY are not set, returns a local dev user so the
app runs fully without Supabase (SQLite fallback mode).
"""

import os
import streamlit as st

_URL = os.getenv("SUPABASE_URL")
_KEY = os.getenv("SUPABASE_KEY")
_USE_SUPABASE = bool(_URL and _KEY)

# Dev user returned when Supabase is not configured (local BYOK dev mode)
_DEV_USER = {"id": "local-dev", "email": "dev@localhost"}


def _client():
    from supabase import create_client
    return create_client(_URL, _KEY)


def get_current_user() -> dict | None:
    """Return the authenticated user dict, or None if not logged in."""
    if not _USE_SUPABASE:
        return _DEV_USER
    return st.session_state.get("user")


def login(email: str, password: str) -> dict:
    """
    Sign in with email + password.
    Returns {"user": {...}} on success, {"error": "..."} on failure.
    """
    if not _USE_SUPABASE:
        return {"user": _DEV_USER}
    try:
        res = _client().auth.sign_in_with_password({"email": email, "password": password})
        if res.user:
            return {"user": {"id": str(res.user.id), "email": res.user.email}}
        return {"error": "Login failed — check your email and password."}
    except Exception as e:
        msg = str(e)
        if "Invalid login" in msg or "invalid_credentials" in msg.lower():
            return {"error": "Incorrect email or password."}
        return {"error": f"Login error: {msg}"}


def signup(email: str, password: str) -> dict:
    """
    Create a new account.
    Returns {"user": {...}} if auto-confirmed, {"needs_confirmation": True} if email
    confirmation is required, or {"error": "..."} on failure.
    """
    if not _USE_SUPABASE:
        return {"user": _DEV_USER}
    try:
        res = _client().auth.sign_up({"email": email, "password": password})
        if res.user and res.session:
            return {"user": {"id": str(res.user.id), "email": res.user.email}}
        if res.user:
            return {"needs_confirmation": True}
        return {"error": "Sign up failed. Try a different email."}
    except Exception as e:
        msg = str(e)
        if "already registered" in msg.lower() or "already exists" in msg.lower():
            return {"error": "An account with that email already exists. Try logging in."}
        return {"error": f"Sign up error: {msg}"}


def logout() -> None:
    """Sign out and clear session state."""
    if _USE_SUPABASE:
        try:
            _client().auth.sign_out()
        except Exception:
            pass
    st.session_state.user = None
