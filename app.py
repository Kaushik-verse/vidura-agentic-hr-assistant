"""Vidura: Streamlit front end.

Run with:  uv run streamlit run app.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta
from html import escape as esc
from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from hr_assistant import agent
from hr_assistant import services as svc
from hr_assistant.config import configure_logging, get_settings
from hr_assistant.db import init_db, reset_db
from hr_assistant.models import (
    ApplyLeave,
    CancelLeave,
    CreateTicket,
    DecideLeave,
    InterviewStatus,
    LeaveStatus,
    LeaveType,
    ListTickets,
    OnboardEmployee,
    Role,
    ScheduleInterview,
    SubmitReview,
    TicketCategory,
    TicketPriority,
    TicketStatus,
    UpdateInterview,
    UpdateTicket,
    User,
)
from hr_assistant.tools import format_validation_error

st.set_page_config(page_title="Vidura", page_icon="🪷", layout="wide", initial_sidebar_state="auto")

configure_logging()
log = logging.getLogger("hr_assistant.ui")
settings = get_settings()


@st.cache_resource
def _bootstrap() -> bool:
    init_db()
    return True


_bootstrap()
st.markdown(f"<style>{(Path(__file__).parent / 'assets' / 'style.css').read_text()}</style>", unsafe_allow_html=True)


# --------------------------------------------------------------------------- helpers


def flash(message: str, icon: str = ":material/check_circle:") -> None:
    st.session_state.setdefault("flash", []).append((message, icon))


def show_flashes() -> None:
    for message, icon in st.session_state.pop("flash", []):
        st.toast(message, icon=icon)


def act(fn, *args, success: str | None = None) -> bool:
    """Run a service call from a form; show validation and permission errors inline."""
    try:
        result = fn(*args)
    except ValidationError as exc:
        st.error(format_validation_error(exc))
        return False
    except svc.HRError as exc:
        st.error(str(exc))
        return False
    except Exception:
        log.exception("UI action failed")
        st.error("Something went wrong. The error has been logged.")
        return False
    message = success or (result.get("message") if isinstance(result, dict) else None) or "Saved."
    flash(message)
    if isinstance(result, dict) and result.get("coverage_warning"):
        flash(result["coverage_warning"], icon=":material/warning:")
    st.rerun()
    return True


DATE_COLS = ["start_date", "end_date"]
DATETIME_COLS = ["created_at", "updated_at", "scheduled_at"]


def table(rows: list[dict], empty: str, columns: dict | None = None, height: int | str = "auto") -> None:
    if not rows:
        st.markdown(f"<div class='empty'>{empty}</div>", unsafe_allow_html=True)
        return
    df = pd.DataFrame(rows)
    for col in df.columns.intersection(DATE_COLS):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%d %b %Y")
    for col in df.columns.intersection(DATETIME_COLS):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%d %b %Y, %H:%M")
    df = df.astype(object).where(df.notna(), "")
    if columns:
        df = df[[c for c in columns if c in df.columns]].rename(columns=columns)
    st.dataframe(df, hide_index=True, width="stretch", height=height)


def section(title: str, caption: str | None = None) -> None:
    st.markdown(
        f"<div class='section'><h3>{title}</h3>{f'<p>{caption}</p>' if caption else ''}</div>",
        unsafe_allow_html=True,
    )


def fmt_day(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%a %d %b")


LEAVE_COLS = {
    "id": "#",
    "employee": "Employee",
    "department": "Team",
    "leave_type": "Type",
    "start_date": "From",
    "end_date": "To",
    "days": "Days",
    "reason": "Reason",
    "status": "Status",
    "decided_by": "Decided by",
}
TICKET_COLS = {
    "id": "#",
    "created_by": "Raised by",
    "category": "Category",
    "priority": "Priority",
    "description": "Description",
    "status": "Status",
    "updated_at": "Updated",
}


# --------------------------------------------------------------------------- sidebar / identity

LOTUS_SVG = (
    "<svg class='brand-mark' viewBox='0 0 32 32' aria-hidden='true'>"
    "<path d='M16 4c3.6 3.4 5.4 7.2 5.4 11.2S19.6 22.6 16 26c-3.6-3.4-5.4-7-5.4-10.8S12.4 7.4 16 4z' fill='#2F5D50'/>"
    "<path d='M4 12c4.7.4 8.3 2.4 10.6 6 1 1.7 1.4 4.3 1.4 8-4.4-.6-7.8-2.4-9.8-5.6C4.9 18.3 4.2 15.5 4 12z' "
    "fill='#2F5D50' opacity='.72'/>"
    "<path d='M28 12c-4.7.4-8.3 2.4-10.6 6-1 1.7-1.4 4.3-1.4 8 4.4-.6 7.8-2.4 9.8-5.6 1.3-2.1 2-4.9 2.2-8.4z' "
    "fill='#2F5D50' opacity='.72'/>"
    "<path d='M6 27.5h20' stroke='#B0703C' stroke-width='2' stroke-linecap='round'/></svg>"
)


def sidebar() -> User:
    with st.sidebar:
        st.markdown(f"<div class='brand'>{LOTUS_SVG}<span>Vidura</span></div>", unsafe_allow_html=True)
        st.caption("Wise counsel for people operations. Named after the Mahabharata's trusted advisor.")

        st.markdown("<p class='label'>Signed in as</p>", unsafe_allow_html=True)
        role = st.segmented_control(
            "Role",
            [Role.EMPLOYEE.value, Role.HR_ADMIN.value],
            default=Role.EMPLOYEE.value,
            key="role",
            label_visibility="collapsed",
            required=True,
        )
        people = svc.list_employees(User(name="System", role=Role.HR_ADMIN))
        if role == Role.HR_ADMIN.value:
            options = [p["name"] for p in people if p["department"] == "People Ops"] or ["HR Admin"]
        else:
            options = [p["name"] for p in people if p["department"] != "People Ops"] or ["Aditi Sharma"]
        options.sort(key=lambda n: (n != "Aditi Sharma", n))  # predictable default for the demo
        name = st.selectbox("Person", options, key=f"person_{role}", label_visibility="collapsed")
        st.caption("Demo sign-in: pick a profile. Swap in SSO for real use.")

        user = User(name=name, role=Role(role))
        if st.session_state.get("chat_owner") != (user.name, user.role):
            st.session_state.chat_owner = (user.name, user.role)
            st.session_state.messages = []

        st.divider()
        if settings.llm_enabled:
            st.markdown(
                f"<p class='status'><i class='dot ok'></i>Assistant online</p>"
                f"<p class='status-model'>{esc(settings.model)}</p>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<p class='status'><i class='dot off'></i>Assistant offline</p>"
                "<p class='status-model'>Set GROQ_API_KEY to enable</p>",
                unsafe_allow_html=True,
            )

        if user.is_admin:
            with st.expander("Demo data"):
                st.caption("Restore the sample company. This deletes everything you've added.")
                if st.button("Reset demo data", type="secondary", width="stretch"):
                    reset_db()
                    st.session_state.messages = []
                    flash("Demo data restored.")
                    st.rerun()
    return user


def header(user: User) -> None:
    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    first = esc(user.name.split()[0])
    st.markdown(
        f"""<header class='page-head'>
              <div><p class='eyebrow'>{svc.today():%A, %d %B %Y}</p>
                   <h1>{greeting}, {first}</h1></div>
              <span class='role-chip'>{esc(user.role.value)}</span>
            </header>""",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- assistant


EMPLOYEE_PROMPTS = [
    "How many leave days do I have left?",
    "Apply for sick leave next Monday for 2 days, I have a fever",
    "My laptop won't turn on, raise an urgent ticket",
    "What's the work from home policy?",
    "Who on my team is out today?",
]
ADMIN_PROMPTS = [
    "Show pending leave requests",
    "Onboard Abhimanyu Rao as a UX Designer in Design",
    "Give me today's HR summary",
    "Which high-priority tickets are open?",
    "Book an interview with Eklavya Singh for Data Analyst next Friday at 3pm with Aditi Sharma",
]


AVATARS = {"user": ":material/person:", "assistant": ":material/support_agent:"}


def _use_suggestion() -> None:
    st.session_state.pending_prompt = st.session_state.suggestion
    st.session_state.suggestion = None


def assistant_tab(user: User) -> None:
    messages: list[dict] = st.session_state.setdefault("messages", [])

    if not settings.llm_enabled:
        st.info(
            "The assistant needs a Groq API key. Add `GROQ_API_KEY` to `.env` and restart. "
            "Every other tab works without it.",
            icon="🔌",
        )
        return

    if not messages:
        st.markdown(
            "<div class='chat-intro'><h3>Ask in plain language</h3><p>The assistant can look things up and take "
            "actions for you. It only does what your role allows.</p></div>",
            unsafe_allow_html=True,
        )
        st.pills(
            "Try",
            ADMIN_PROMPTS if user.is_admin else EMPLOYEE_PROMPTS,
            key="suggestion",
            on_change=_use_suggestion,
            label_visibility="collapsed",
        )

    for m in messages:
        with st.chat_message(m["role"], avatar=AVATARS[m["role"]]):
            st.markdown(m["content"])

    prompt = st.chat_input("Ask about leave, tickets, policies…", max_chars=agent.MAX_INPUT_CHARS)
    prompt = prompt or st.session_state.pop("pending_prompt", None)
    if not prompt:
        if messages and st.button("Clear conversation", type="tertiary"):
            st.session_state.messages = []
            st.rerun()
        return

    with st.chat_message("user", avatar=AVATARS["user"]):
        st.markdown(prompt)
    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        with st.spinner("Working on it…"):
            history = [(m["role"], m["content"]) for m in messages]
            try:
                reply = agent.chat(user, prompt, history)
            except Exception as exc:
                log.exception("Assistant failed")
                st.error(agent.friendly_error(exc))
                return
        st.markdown(reply)
    messages += [{"role": "user", "content": prompt}, {"role": "assistant", "content": reply}]
    st.rerun()  # refresh other tabs with any data the assistant changed


# --------------------------------------------------------------------------- employee views


def my_leave_tab(user: User) -> None:
    try:
        balance = svc.leave_balance(user)
    except svc.HRError as exc:
        st.warning(str(exc))
        return
    history = svc.leave_history(user)
    pending = [h for h in history if h["status"] == LeaveStatus.PENDING]

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Annual leave left",
        f"{balance['annual_remaining']} days",
        help=f"{balance['annual_allowance']}-day allowance; pending requests are reserved.",
        border=True,
    )
    c2.metric("Awaiting approval", len(pending), border=True)
    c3.metric("Sick days this year", balance["sick_days_taken"], border=True)

    left, right = st.columns([2, 3], gap="large")
    with left:
        section("Request time off", "Working days only. Weekends are skipped automatically.")
        with st.form("leave_form", clear_on_submit=True, border=True):
            start = st.date_input("First day", min_value=svc.today(), value=None, format="DD/MM/YYYY")
            c_a, c_b = st.columns(2)
            days = c_a.number_input("Working days", min_value=1, max_value=30, value=1)
            ltype = c_b.selectbox("Type", [t.value for t in LeaveType], format_func=str.capitalize)
            reason = st.text_input("Reason", placeholder="e.g. Family visit")
            if st.form_submit_button("Submit request", type="primary", width="stretch"):
                if start is None:
                    st.error("Pick a start date.")
                else:
                    act(
                        lambda: svc.apply_leave(
                            user, ApplyLeave(start_date=start, days=int(days), leave_type=ltype, reason=reason)
                        )
                    )
    with right:
        section("My requests")
        table(history, "No leave requests yet.", LEAVE_COLS)
        cancellable = [
            h for h in history if h["status"] in ("Pending", "Approved") and h["start_date"] > svc.today().isoformat()
        ]
        if cancellable:
            with st.popover("Cancel a request"):
                pick = st.selectbox(
                    "Request",
                    cancellable,
                    format_func=lambda h: f"#{h['id']} · {fmt_day(h['start_date'])} · {h['days']}d",
                )
                if st.button("Cancel request", type="primary"):
                    act(lambda: svc.cancel_leave(user, CancelLeave(leave_id=pick["id"])))


def ticket_form(user: User) -> None:
    section("Raise a ticket", "Payroll, IT, benefits or anything else HR can help with.")
    with st.form("ticket_form", clear_on_submit=True, border=True):
        c_a, c_b = st.columns(2)
        category = c_a.selectbox("Category", [c.value for c in TicketCategory], format_func=str.capitalize)
        priority = c_b.selectbox("Priority", [p.value for p in TicketPriority], index=1, format_func=str.capitalize)
        description = st.text_area("What do you need?", height=110)
        if st.form_submit_button("Open ticket", type="primary", width="stretch"):
            act(
                lambda: svc.create_ticket(
                    user, CreateTicket(category=category, priority=priority, description=description)
                )
            )


def my_tickets_tab(user: User) -> None:
    left, right = st.columns([2, 3], gap="large")
    with left:
        ticket_form(user)
    with right:
        section("My tickets")
        table(svc.list_tickets(user), "You haven't raised any tickets.", TICKET_COLS)
        section("My reviews")
        try:
            reviews = svc.list_reviews(user)
        except svc.HRError:
            reviews = []
        table(
            reviews,
            "No performance reviews yet.",
            {"created_at": "Date", "reviewer": "Reviewer", "rating": "Rating", "comments": "Comments"},
        )


def team_tab(user: User) -> None:
    left, right = st.columns([2, 3], gap="large")
    with left:
        section("Out today")
        out = svc.who_is_out(user)
        if out:
            items = "".join(
                f"<li><strong>{esc(r['name'])}</strong>"
                f"<span>{esc(r['department'])} · back after {fmt_day(r['end_date'])}</span></li>"
                for r in out
            )
            st.markdown(f"<ul class='people'>{items}</ul>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='empty'>Everyone's in today.</div>", unsafe_allow_html=True)
    with right:
        section("Directory")
        table(
            svc.list_employees(user),
            "No employees yet.",
            {"name": "Name", "job_title": "Title", "department": "Team", "start_date": "Joined"},
        )


# --------------------------------------------------------------------------- admin views


def overview_tab(user: User) -> None:
    s = svc.analytics(user)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Headcount", s["headcount"], border=True)
    c2.metric("Out today", s["on_leave_today"], border=True)
    c3.metric("Leave to review", s["pending_leave_requests"], border=True)
    c4.metric(
        "Open tickets",
        s["open_tickets"],
        delta=f"{s['open_high_priority_tickets']} high priority",
        delta_color="off",
        delta_arrow="off",
        border=True,
    )

    def chart(title: str, data: dict, label: str) -> None:
        with st.container(border=True):
            st.markdown(f"<p class='chart-title'>{title}</p>", unsafe_allow_html=True)
            if data:
                df = pd.DataFrame({label: list(data.keys()), "Count": list(data.values())})
                st.bar_chart(
                    df, x=label, y="Count", horizontal=True, color="#2F5D50", height=200, x_label="", y_label=""
                )
            else:
                st.markdown("<div class='empty'>No data yet.</div>", unsafe_allow_html=True)

    a, b, c = st.columns(3)
    with a:
        chart("Headcount by team", s["headcount_by_department"], "Team")
    with b:
        chart("Tickets by category", s["tickets_by_category"], "Category")
    with c:
        chart("Leave requests by status", s["leaves_by_status"], "Status")

    left, right = st.columns(2, gap="large")
    with left:
        section("Onboarding in progress")
        rows = svc.onboarding_status(user)
        if rows:
            for r in rows:
                done, total = int(r["done"] or 0), int(r["tasks"])
                st.markdown(f"**{r['name']}** · {r['job_title']}, {r['department']}")
                st.progress(done / total, text=f"{done} of {total} setup tasks done")
        else:
            st.markdown("<div class='empty'>No one is mid-onboarding.</div>", unsafe_allow_html=True)
    with right:
        section("Out this week")
        week_end = svc.today() + timedelta(days=6)
        out = [
            r
            for r in svc.list_leaves(user, LeaveStatus.APPROVED)
            if r["start_date"] <= week_end.isoformat() and r["end_date"] >= svc.today().isoformat()
        ]
        table(
            out,
            "Nobody has approved leave this week.",
            {
                "employee": "Employee",
                "department": "Team",
                "start_date": "From",
                "end_date": "To",
            },
        )


def leave_admin_tab(user: User) -> None:
    pending = svc.list_leaves(user, LeaveStatus.PENDING)
    section("Waiting for a decision", f"{len(pending)} request(s)")
    if not pending:
        st.markdown("<div class='empty'>Inbox zero. No leave to review.</div>", unsafe_allow_html=True)
    for r in pending:
        with st.container(border=True):
            info, actions = st.columns([4, 2], vertical_alignment="center")
            info.markdown(
                f"**{r['employee']}** · {r['department']}  \n"
                f"{r['leave_type'].capitalize()} · {fmt_day(r['start_date'])} → {fmt_day(r['end_date'])} "
                f"({r['days']} day{'s' if r['days'] > 1 else ''}) · _{r['reason']}_"
            )
            approve, reject = actions.columns(2)
            if approve.button("Approve", key=f"ap{r['id']}", type="primary", width="stretch"):
                act(lambda r=r: svc.decide_leave(user, DecideLeave(leave_id=r["id"], decision="Approved")))
            if reject.button("Reject", key=f"rj{r['id']}", width="stretch"):
                act(lambda r=r: svc.decide_leave(user, DecideLeave(leave_id=r["id"], decision="Rejected")))

    section("All requests")
    status = st.segmented_control(
        "Filter",
        ["All", *[s.value for s in LeaveStatus]],
        default="All",
        key="leave_filter",
        label_visibility="collapsed",
    )
    rows = svc.list_leaves(user, None if status in (None, "All") else LeaveStatus(status))
    table(rows, "No requests match.", LEAVE_COLS)


def tickets_admin_tab(user: User) -> None:
    status = st.segmented_control(
        "Status",
        ["All", *[s.value for s in TicketStatus]],
        default="Open",
        key="ticket_filter",
        label_visibility="collapsed",
    )
    rows = svc.list_tickets(user, ListTickets(status=None if status in (None, "All") else status))
    table(rows, "No tickets match.", TICKET_COLS)

    left, right = st.columns(2, gap="large")
    with left:
        section("Update a ticket")
        open_rows = [t for t in svc.list_tickets(user) if t["status"] != TicketStatus.CLOSED]
        if open_rows:
            with st.form("ticket_update", border=True):
                pick = st.selectbox(
                    "Ticket", open_rows, format_func=lambda t: f"#{t['id']} · {t['category']} · {t['description'][:50]}"
                )
                new_status = st.selectbox("New status", [s.value for s in TicketStatus])
                if st.form_submit_button("Update", type="primary"):
                    act(lambda: svc.update_ticket(user, UpdateTicket(ticket_id=pick["id"], status=new_status)))
        else:
            st.markdown("<div class='empty'>Nothing open.</div>", unsafe_allow_html=True)
    with right:
        ticket_form(user)


def people_tab(user: User) -> None:
    left, right = st.columns(2, gap="large")
    with left:
        section("Onboard a new hire", "Creates the employee plus IT, payroll and orientation tickets.")
        with st.form("onboard", clear_on_submit=True, border=True):
            name = st.text_input("Full name")
            c_a, c_b = st.columns(2)
            title = c_a.text_input("Job title")
            dept = c_b.text_input("Team", placeholder="Engineering")
            start = st.date_input("Start date", value=svc.today(), format="DD/MM/YYYY")
            if st.form_submit_button("Onboard", type="primary", width="stretch"):
                act(
                    lambda: svc.onboard_employee(
                        user, OnboardEmployee(name=name, job_title=title, department=dept, start_date=start)
                    )
                )
    with right:
        section("Write a performance review")
        others = [n for n in svc.employee_names() if n != user.name]
        with st.form("review", clear_on_submit=True, border=True):
            who = st.selectbox("Employee", others)
            rating = st.slider("Rating", 1, 5, 4)
            comments = st.text_area("Comments", height=90)
            if st.form_submit_button("Save review", type="primary", width="stretch"):
                act(lambda: svc.submit_review(user, SubmitReview(employee_name=who, rating=rating, comments=comments)))
    section("Directory")
    table(
        svc.list_employees(user),
        "No employees yet.",
        {"id": "#", "name": "Name", "job_title": "Title", "department": "Team", "start_date": "Joined"},
    )
    section("Recent reviews")
    table(
        svc.list_reviews(user),
        "No reviews yet.",
        {
            "created_at": "Date",
            "employee": "Employee",
            "reviewer": "Reviewer",
            "rating": "Rating",
            "comments": "Comments",
        },
    )


def hiring_tab(user: User) -> None:
    upcoming = svc.list_interviews(user)
    section("Upcoming interviews", f"{len(upcoming)} scheduled")
    table(
        upcoming,
        "No interviews scheduled.",
        {
            "id": "#",
            "scheduled_at": "When",
            "candidate_name": "Candidate",
            "position": "Role",
            "interviewer": "Interviewer",
        },
    )
    left, right = st.columns(2, gap="large")
    with left:
        section("Book an interview")
        with st.form("interview", clear_on_submit=True, border=True):
            cand = st.text_input("Candidate")
            pos = st.text_input("Role")
            interviewer = st.selectbox("Interviewer", svc.employee_names())
            c_a, c_b = st.columns(2)
            day = c_a.date_input(
                "Date", min_value=svc.today(), value=svc.today() + timedelta(days=1), format="DD/MM/YYYY"
            )
            at = c_b.time_input("Time", value=time(10, 0), step=timedelta(minutes=15))
            if st.form_submit_button("Book", type="primary", width="stretch"):
                act(
                    lambda: svc.schedule_interview(
                        user,
                        ScheduleInterview(
                            candidate_name=cand,
                            position=pos,
                            interviewer=interviewer,
                            interview_date=day,
                            start_time=at.strftime("%H:%M"),
                        ),
                    )
                )
    with right:
        section("Record an outcome")
        if upcoming:
            with st.form("interview_status", border=True):
                pick = st.selectbox(
                    "Interview",
                    upcoming,
                    format_func=lambda i: (
                        f"#{i['id']} · {i['candidate_name']} · {datetime.fromisoformat(i['scheduled_at']):%d %b, %H:%M}"
                    ),
                )
                status = st.selectbox("Outcome", [s.value for s in InterviewStatus if s != InterviewStatus.SCHEDULED])
                if st.form_submit_button("Save", type="primary"):
                    act(lambda: svc.update_interview(user, UpdateInterview(interview_id=pick["id"], status=status)))
        else:
            st.markdown("<div class='empty'>Nothing to record.</div>", unsafe_allow_html=True)


def activity_tab(user: User) -> None:
    section("Audit log", "Every change, whether made from a form or by the assistant.")
    rows = svc.audit_log(user, limit=200)
    for r in rows:
        r["action"] = r["action"].replace("_", " ").capitalize()
        r["details"] = " · ".join(f"{k.replace('_', ' ')}: {v}" for k, v in json.loads(r["details"]).items())
    table(
        rows,
        "No activity yet.",
        {"created_at": "When", "actor": "Who", "role": "Role", "action": "Action", "details": "Details"},
    )


# --------------------------------------------------------------------------- page


def main() -> None:
    user = sidebar()
    show_flashes()
    header(user)

    if user.is_admin:
        views = {
            "Overview": overview_tab,
            "Assistant": assistant_tab,
            "Leave": leave_admin_tab,
            "Tickets": tickets_admin_tab,
            "People": people_tab,
            "Hiring": hiring_tab,
            "Activity": activity_tab,
        }
    else:
        views = {
            "Assistant": assistant_tab,
            "My leave": my_leave_tab,
            "Tickets & reviews": my_tickets_tab,
            "Team": team_tab,
        }

    for tab, render in zip(st.tabs(list(views), key=f"tabs_{user.role.value}"), views.values(), strict=True):
        with tab:
            render(user)


main()
