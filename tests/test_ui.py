"""Smoke tests: every page renders for both roles without exceptions."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def run_app(role: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    if role != "Employee":
        at.session_state["role"] = role
        at.run()
    return at


@pytest.mark.parametrize("role", ["Employee", "HR Admin"])
def test_pages_render(role):
    at = run_app(role)
    assert not at.exception, at.exception
    labels = [t.label for t in at.tabs]
    if role == "HR Admin":
        assert labels == ["Overview", "Assistant", "Leave", "Tickets", "People", "Hiring", "Activity"]
    else:
        assert labels == ["Assistant", "My leave", "Tickets & reviews", "Team"]
    # No API key in tests, so the assistant explains how to enable itself.
    assert any("GROQ_API_KEY" in i.value for i in at.info)


def test_admin_can_approve_leave_from_ui():
    at = run_app("HR Admin")
    approve = next(b for b in at.button if b.label == "Approve")
    approve.click().run()
    assert not at.exception
    from hr_assistant.db import query

    assert query("SELECT COUNT(*) AS n FROM leaves WHERE status = 'Pending'")[0]["n"] == 1
