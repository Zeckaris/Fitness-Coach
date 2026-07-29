"""
Login/register UI components and session gating logic for the Streamlit app.

Keeps auth-related UI out of streamlit_app.py, which owns page layout and
routing, not credential forms.
"""

import streamlit as st

from auth.user_store import (
    authenticate,
    create_user,
    InvalidEmailError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
)
from auth.context import set_current_user_id, clear_current_user_id
from db.mongo_client import ensure_email_index


def render_login_form():
    """Renders the login/register tabs. Sets st.session_state['user_id']
    and the auth context on success, then reruns."""
    st.title("🏋️ AI Fitness Coach")
    st.caption("Log in to continue.")

    login_tab, register_tab = st.tabs(["Log In", "Register"])

    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log In", use_container_width=True)

            if submitted:
                try:
                    user_doc = authenticate(email, password)
                    st.session_state["user_id"] = str(user_doc["_id"])
                    set_current_user_id(st.session_state["user_id"])
                    st.rerun()
                except InvalidCredentialsError:
                    st.error("Invalid email or password.")

    with register_tab:
        with st.form("register_form"):
            email = st.text_input("Email", key="register_email")
            password = st.text_input("Password", type="password", key="register_password")
            confirm = st.text_input("Confirm Password", type="password", key="register_confirm")
            submitted = st.form_submit_button("Register", use_container_width=True)

            if submitted:
                if password != confirm:
                    st.error("Passwords do not match.")
                elif len(password) < 8:
                    st.error("Password must be at least 8 characters.")
                else:
                    try:
                        user_doc = create_user(email, password)
                        st.session_state["user_id"] = str(user_doc["_id"])
                        set_current_user_id(st.session_state["user_id"])
                        st.success("Account created!")
                        st.rerun()
                    except InvalidEmailError as e:
                        st.error(f"Invalid email: {e}")
                    except EmailAlreadyRegisteredError:
                        st.error("An account with this email already exists.")


def ensure_logged_in():
    """
    Call once near the top of the app, after st.set_page_config().

    Ensures the email uniqueness index exists (once per process), then
    gates the rest of the app behind login: renders the login form and
    halts execution if no user is logged in, otherwise re-establishes the
    auth context for this rerun (required every rerun - ContextVar state
    doesn't persist across Streamlit reruns the way session_state does).
    """
    if "email_index_ready" not in st.session_state:
        ensure_email_index()
        st.session_state.email_index_ready = True

    if "user_id" not in st.session_state:
        render_login_form()
        st.stop()

    set_current_user_id(st.session_state["user_id"])


def render_logout_button():
    """Renders a logout button. Clears auth + session-scoped app state."""
    if st.button("🚪 Log Out"):
        clear_current_user_id()
        for key in ["user_id", "thread_id", "history", "graph", "workout_state"]:
            st.session_state.pop(key, None)
        st.rerun()