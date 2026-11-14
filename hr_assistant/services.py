"""HR business logic.

Every public function takes the signed-in ``User`` first and enforces role-based access here,
so the rules hold no matter who calls them: the LLM agent, the MCP server or the UI.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from difflib import get_close_matches

from . import db
from .config import get_settings
from .models import (
    ApplyLeave,
    CancelLeave,
    CreateTicket,
    DecideLeave,
    EmployeeRef,
    LeaveStatus,
    LeaveType,
    ListEmployees,
    ListTickets,
    OnboardEmployee,
    PolicyLookup,
    ScheduleInterview,
    SubmitReview,
    TicketStatus,
    UpdateInterview,
    UpdateTicket,
    User,
    WhoIsOut,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- errors


class HRError(Exception):
    """Base class for expected, user-facing errors."""


class PermissionDenied(HRError):
    pass


class NotFound(HRError):
    pass


class InvalidRequest(HRError):
    pass


# --------------------------------------------------------------------------- helpers


def today() -> date:
    """Indirection so tests can freeze the clock."""
    return date.today()


def now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _require_admin(user: User, action: str) -> None:
    if not user.is_admin:
        raise PermissionDenied(f"Only HR Admins can {action}.")


def _audit(conn: sqlite3.Connection, user: User, action: str, **details: object) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor, role, action, details) VALUES (?, ?, ?, ?)",
        (user.name, user.role.value, action, json.dumps(details, default=str)),
    )
    log.info("%s (%s) %s %s", user.name, user.role.value, action, details)


def _employee(conn: sqlite3.Connection, name: str) -> dict:
    row = conn.execute("SELECT * FROM employees WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row is None:
        names = [r[0] for r in conn.execute("SELECT name FROM employees").fetchall()]
        hint = get_close_matches(name, names, n=1)
        suffix = f" Did you mean '{hint[0]}'?" if hint else ""
        raise NotFound(f"No employee named '{name}'.{suffix}")
    return dict(row)


def _target_employee(conn: sqlite3.Connection, user: User, requested: str | None) -> dict:
    """Employees may only act on their own record; admins may act on anyone's."""
    if requested and requested.casefold() != user.name.casefold():
        if not user.is_admin:
            raise PermissionDenied("Employees can only access their own records.")
        return _employee(conn, requested)
    try:
        return _employee(conn, user.name)
    except NotFound as exc:
        if user.is_admin:
            raise InvalidRequest("Please say which employee this is for.") from exc
        raise NotFound(f"'{user.name}' is not on the employee register.") from exc


def add_working_days(start: date, days: int) -> date:
    """Return the last working day of a ``days``-long leave starting on ``start``."""
    current = start
    counted = 1
    while counted < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            counted += 1
    return current


# --------------------------------------------------------------------------- employees


def onboard_employee(user: User, data: OnboardEmployee) -> dict:
    """Create the employee and their IT, payroll and orientation tickets in one transaction."""
    _require_admin(user, "onboard employees")
    start = data.start_date or today()
    tasks = [
        ("technical", "high", f"Provision laptop, email and system access for {data.name} ({data.job_title})."),
        ("payroll", "medium", f"Set up direct deposit and tax forms for {data.name}."),
        ("onboarding", "medium", f"Schedule orientation and assign a buddy for {data.name} in {data.department}."),
    ]
    with db.connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO employees (name, job_title, department, start_date) VALUES (?, ?, ?, ?)",
                (data.name, data.job_title, data.department, start.isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise InvalidRequest(f"An employee named '{data.name}' already exists.") from exc
        employee_id = cur.lastrowid
        ticket_ids = []
        for category, priority, description in tasks:
            cur = conn.execute(
                "INSERT INTO tickets (created_by, employee_id, category, priority, description, onboarding_task) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (user.name, employee_id, category, priority, description),
            )
            ticket_ids.append(cur.lastrowid)
        _audit(conn, user, "onboard_employee", employee_id=employee_id, name=data.name, tickets=ticket_ids)
    return {
        "message": f"Onboarded {data.name} as {data.job_title} ({data.department}), starting {start}. "
        f"Created tickets #{', #'.join(map(str, ticket_ids))} for IT, payroll and orientation.",
        "employee_id": employee_id,
        "ticket_ids": ticket_ids,
    }


def list_employees(user: User, data: ListEmployees | None = None) -> list[dict]:
    """Company directory (name, title, department) is visible to everyone."""
    data = data or ListEmployees()
    sql = "SELECT id, name, job_title, department, start_date FROM employees"
    params: tuple = ()
    if data.department:
        sql += " WHERE department = ? COLLATE NOCASE"
        params = (data.department,)
    return db.query(sql + " ORDER BY department, name", params)


def employee_names() -> list[str]:
    return [r["name"] for r in db.query("SELECT name FROM employees ORDER BY name")]


def onboarding_status(user: User) -> list[dict]:
    """Progress of onboarding tickets per new hire."""
    _require_admin(user, "view onboarding progress")
    return db.query(
        """
        SELECT e.name, e.job_title, e.department, e.start_date,
               COUNT(t.id) AS tasks,
               SUM(t.status IN ('Resolved', 'Closed')) AS done
        FROM employees e JOIN tickets t ON t.employee_id = e.id AND t.onboarding_task = 1
        GROUP BY e.id
        HAVING SUM(t.status IN ('Resolved', 'Closed')) < COUNT(t.id)
        ORDER BY e.start_date DESC
        """
    )


# --------------------------------------------------------------------------- leave


def _annual_days_used(conn: sqlite3.Connection, employee_id: int, year: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(days), 0) FROM leaves WHERE employee_id = ? AND leave_type = 'annual' "
        "AND status IN ('Pending', 'Approved') AND substr(start_date, 1, 4) = ?",
        (employee_id, str(year)),
    ).fetchone()
    return int(row[0])


def leave_balance(user: User, data: EmployeeRef | None = None) -> dict:
    data = data or EmployeeRef()
    year = today().year
    allowance = get_settings().annual_leave_days
    with db.connect() as conn:
        emp = _target_employee(conn, user, data.employee_name)
        used = _annual_days_used(conn, emp["id"], year)
        sick = conn.execute(
            "SELECT COALESCE(SUM(days), 0) FROM leaves WHERE employee_id = ? AND leave_type = 'sick' "
            "AND status = 'Approved' AND substr(start_date, 1, 4) = ?",
            (emp["id"], str(year)),
        ).fetchone()[0]
    return {
        "employee": emp["name"],
        "year": year,
        "annual_allowance": allowance,
        "annual_used_or_pending": used,
        "annual_remaining": allowance - used,
        "sick_days_taken": int(sick),
    }


def apply_leave(user: User, data: ApplyLeave) -> dict:
    if data.start_date < today():
        raise InvalidRequest("Leave cannot start in the past.")
    if data.start_date.weekday() >= 5:
        raise InvalidRequest(f"{data.start_date:%A %d %b} is a weekend. Leave must start on a working day.")
    end = add_working_days(data.start_date, data.days)
    settings = get_settings()

    with db.connect() as conn:
        emp = _target_employee(conn, user, data.employee_name)

        overlap = conn.execute(
            "SELECT id FROM leaves WHERE employee_id = ? AND status IN ('Pending', 'Approved') "
            "AND start_date <= ? AND end_date >= ?",
            (emp["id"], end.isoformat(), data.start_date.isoformat()),
        ).fetchone()
        if overlap:
            raise InvalidRequest(f"This overlaps existing leave request #{overlap[0]}.")

        if data.leave_type is LeaveType.ANNUAL:
            used = _annual_days_used(conn, emp["id"], data.start_date.year)
            remaining = settings.annual_leave_days - used
            if data.days > remaining:
                raise InvalidRequest(
                    f"Not enough annual leave: {remaining} day(s) left, {data.days} requested. Consider unpaid leave."
                )

        colleagues = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT e.name FROM leaves l JOIN employees e ON e.id = l.employee_id "
                "WHERE e.department = ? AND e.id != ? AND l.status = 'Approved' "
                "AND l.start_date <= ? AND l.end_date >= ?",
                (emp["department"], emp["id"], end.isoformat(), data.start_date.isoformat()),
            ).fetchall()
        ]
        cur = conn.execute(
            "INSERT INTO leaves (employee_id, leave_type, start_date, end_date, days, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (emp["id"], data.leave_type.value, data.start_date.isoformat(), end.isoformat(), data.days, data.reason),
        )
        leave_id = cur.lastrowid
        _audit(conn, user, "apply_leave", leave_id=leave_id, employee=emp["name"], days=data.days)

    message = (
        f"Leave request #{leave_id} submitted for {emp['name']}: {data.leave_type.value}, "
        f"{data.start_date:%a %d %b} to {end:%a %d %b %Y} ({data.days} working day(s)). Status: Pending."
    )
    warning = None
    if len(colleagues) >= settings.coverage_threshold:
        warning = (
            f"Low coverage in {emp['department']}: {', '.join(colleagues)} already approved for leave "
            "in this period. Approval may be delayed."
        )
        message += f"\nWarning: {warning}"
    return {"message": message, "leave_id": leave_id, "end_date": end.isoformat(), "coverage_warning": warning}


def leave_history(user: User, data: EmployeeRef | None = None) -> list[dict]:
    data = data or EmployeeRef()
    with db.connect() as conn:
        emp = _target_employee(conn, user, data.employee_name)
        rows = conn.execute(
            "SELECT id, leave_type, start_date, end_date, days, reason, status, decided_by "
            "FROM leaves WHERE employee_id = ? ORDER BY start_date DESC",
            (emp["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


def list_leaves(user: User, status: LeaveStatus | None = None) -> list[dict]:
    _require_admin(user, "view all leave requests")
    sql = (
        "SELECT l.id, e.name AS employee, e.department, l.leave_type, l.start_date, l.end_date, "
        "l.days, l.reason, l.status, l.decided_by FROM leaves l JOIN employees e ON e.id = l.employee_id"
    )
    params: tuple = ()
    if status:
        sql += " WHERE l.status = ?"
        params = (status.value,)
    return db.query(sql + " ORDER BY l.start_date DESC", params)


def decide_leave(user: User, data: DecideLeave) -> dict:
    _require_admin(user, "approve or reject leave")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT l.status, e.name FROM leaves l JOIN employees e ON e.id = l.employee_id WHERE l.id = ?",
            (data.leave_id,),
        ).fetchone()
        if row is None:
            raise NotFound(f"Leave request #{data.leave_id} does not exist.")
        if row["status"] != LeaveStatus.PENDING:
            raise InvalidRequest(f"Leave request #{data.leave_id} is already {row['status']}.")
        if row["name"].casefold() == user.name.casefold():
            raise PermissionDenied("You cannot approve your own leave request.")
        conn.execute(
            "UPDATE leaves SET status = ?, decided_by = ?, decided_at = datetime('now', 'localtime') WHERE id = ?",
            (data.decision.value, user.name, data.leave_id),
        )
        _audit(conn, user, "decide_leave", leave_id=data.leave_id, decision=data.decision.value)
    return {"message": f"Leave request #{data.leave_id} for {row['name']} {data.decision.value.lower()}."}


def cancel_leave(user: User, data: CancelLeave) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT l.status, l.start_date, e.name FROM leaves l JOIN employees e ON e.id = l.employee_id "
            "WHERE l.id = ?",
            (data.leave_id,),
        ).fetchone()
        if row is None or (not user.is_admin and row["name"].casefold() != user.name.casefold()):
            # Don't reveal whether someone else's request exists.
            raise NotFound(f"Leave request #{data.leave_id} not found.")
        if row["status"] not in (LeaveStatus.PENDING, LeaveStatus.APPROVED):
            raise InvalidRequest(f"Leave request #{data.leave_id} is already {row['status']}.")
        if row["start_date"] <= today().isoformat():
            raise InvalidRequest("Leave that has already started cannot be cancelled. Contact HR.")
        conn.execute("UPDATE leaves SET status = 'Cancelled' WHERE id = ?", (data.leave_id,))
        _audit(conn, user, "cancel_leave", leave_id=data.leave_id)
    return {"message": f"Leave request #{data.leave_id} cancelled."}


def who_is_out(user: User, data: WhoIsOut | None = None) -> list[dict]:
    data = data or WhoIsOut()
    day = (data.on_date or today()).isoformat()
    sql = (
        "SELECT e.name, e.department, l.leave_type, l.start_date, l.end_date FROM leaves l "
        "JOIN employees e ON e.id = l.employee_id "
        "WHERE l.status = 'Approved' AND l.start_date <= ? AND l.end_date >= ?"
    )
    params: list = [day, day]
    if data.department:
        sql += " AND e.department = ? COLLATE NOCASE"
        params.append(data.department)
    rows = db.query(sql + " ORDER BY e.department, e.name", tuple(params))
    if not user.is_admin:
        # Colleagues see who is away, not why.
        for r in rows:
            r.pop("leave_type")
    return rows


# --------------------------------------------------------------------------- tickets


def create_ticket(user: User, data: CreateTicket) -> dict:
    with db.connect() as conn:
        emp = conn.execute("SELECT id FROM employees WHERE name = ? COLLATE NOCASE", (user.name,)).fetchone()
        cur = conn.execute(
            "INSERT INTO tickets (created_by, employee_id, category, priority, description) VALUES (?, ?, ?, ?, ?)",
            (user.name, emp[0] if emp else None, data.category.value, data.priority.value, data.description),
        )
        ticket_id = cur.lastrowid
        _audit(conn, user, "create_ticket", ticket_id=ticket_id, category=data.category.value)
    return {
        "message": f"Ticket #{ticket_id} opened ({data.category.value}, {data.priority.value} priority).",
        "ticket_id": ticket_id,
    }


def list_tickets(user: User, data: ListTickets | None = None) -> list[dict]:
    data = data or ListTickets()
    sql = "SELECT id, created_by, category, priority, description, status, created_at, updated_at FROM tickets"
    clauses, params = [], []
    if not user.is_admin:
        clauses.append("created_by = ? COLLATE NOCASE")
        params.append(user.name)
    if data.status:
        clauses.append("status = ?")
        params.append(data.status.value)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id DESC"
    return db.query(sql, tuple(params))


def update_ticket(user: User, data: UpdateTicket) -> dict:
    _require_admin(user, "update ticket status")
    with db.connect() as conn:
        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (data.ticket_id,)).fetchone()
        if row is None:
            raise NotFound(f"Ticket #{data.ticket_id} does not exist.")
        if row["status"] == TicketStatus.CLOSED:
            raise InvalidRequest(f"Ticket #{data.ticket_id} is closed and cannot be changed.")
        conn.execute(
            "UPDATE tickets SET status = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (data.status.value, data.ticket_id),
        )
        _audit(conn, user, "update_ticket", ticket_id=data.ticket_id, status=data.status.value)
    return {"message": f"Ticket #{data.ticket_id} moved from {row['status']} to {data.status.value}."}


# --------------------------------------------------------------------------- interviews


def schedule_interview(user: User, data: ScheduleInterview) -> dict:
    _require_admin(user, "schedule interviews")
    when = data.scheduled_at
    if when <= now():
        raise InvalidRequest("Interviews must be scheduled in the future.")
    if when.weekday() >= 5:
        raise InvalidRequest(f"{when:%A %d %b} is a weekend. Pick a working day.")
    with db.connect() as conn:
        clash = conn.execute(
            "SELECT candidate_name, scheduled_at FROM interviews WHERE status = 'Scheduled' "
            "AND interviewer = ? COLLATE NOCASE AND scheduled_at > ? AND scheduled_at < ?",
            (data.interviewer, (when - timedelta(hours=1)).isoformat(), (when + timedelta(hours=1)).isoformat()),
        ).fetchone()
        if clash:
            raise InvalidRequest(
                f"{data.interviewer} already has an interview with {clash[0]} at {clash[1]}. Pick another slot."
            )
        cur = conn.execute(
            "INSERT INTO interviews (candidate_name, position, interviewer, scheduled_at) VALUES (?, ?, ?, ?)",
            (data.candidate_name, data.position, data.interviewer, when.isoformat()),
        )
        interview_id = cur.lastrowid
        _audit(conn, user, "schedule_interview", interview_id=interview_id, candidate=data.candidate_name)
    return {
        "message": f"Interview #{interview_id} booked: {data.candidate_name} for {data.position} with "
        f"{data.interviewer} on {when:%a %d %b %Y at %H:%M}.",
        "interview_id": interview_id,
    }


def list_interviews(user: User, upcoming_only: bool = True) -> list[dict]:
    _require_admin(user, "view interviews")
    sql = "SELECT id, candidate_name, position, interviewer, scheduled_at, status FROM interviews"
    params: tuple = ()
    if upcoming_only:
        sql += " WHERE status = 'Scheduled' AND scheduled_at >= ?"
        params = (now().isoformat(),)
    return db.query(sql + " ORDER BY scheduled_at", params)


def update_interview(user: User, data: UpdateInterview) -> dict:
    _require_admin(user, "update interviews")
    with db.connect() as conn:
        row = conn.execute("SELECT status FROM interviews WHERE id = ?", (data.interview_id,)).fetchone()
        if row is None:
            raise NotFound(f"Interview #{data.interview_id} does not exist.")
        conn.execute("UPDATE interviews SET status = ? WHERE id = ?", (data.status.value, data.interview_id))
        _audit(conn, user, "update_interview", interview_id=data.interview_id, status=data.status.value)
    return {"message": f"Interview #{data.interview_id} marked {data.status.value}."}


# --------------------------------------------------------------------------- reviews


def submit_review(user: User, data: SubmitReview) -> dict:
    _require_admin(user, "submit performance reviews")
    with db.connect() as conn:
        emp = _employee(conn, data.employee_name)
        if emp["name"].casefold() == user.name.casefold():
            raise PermissionDenied("You cannot review yourself.")
        cur = conn.execute(
            "INSERT INTO performance_reviews (employee_id, reviewer, rating, comments) VALUES (?, ?, ?, ?)",
            (emp["id"], user.name, data.rating, data.comments),
        )
        _audit(conn, user, "submit_review", review_id=cur.lastrowid, employee=emp["name"])
    return {"message": f"Performance review for {emp['name']} saved ({data.rating}/5).", "review_id": cur.lastrowid}


def list_reviews(user: User, data: EmployeeRef | None = None) -> list[dict]:
    data = data or EmployeeRef()
    with db.connect() as conn:
        if user.is_admin and not data.employee_name:
            rows = conn.execute(
                "SELECT r.id, e.name AS employee, r.reviewer, r.rating, r.comments, r.created_at "
                "FROM performance_reviews r JOIN employees e ON e.id = r.employee_id ORDER BY r.id DESC"
            ).fetchall()
        else:
            emp = _target_employee(conn, user, data.employee_name)
            rows = conn.execute(
                "SELECT id, reviewer, rating, comments, created_at FROM performance_reviews "
                "WHERE employee_id = ? ORDER BY id DESC",
                (emp["id"],),
            ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- policies

POLICIES: dict[str, str] = {
    "wfh": "Employees may work from home up to 2 days per week with manager approval. "
    "Core collaboration hours are 11:00-16:00 local time.",
    "leave": "Full-time employees get {annual} days of paid annual leave per calendar year; unused days do not "
    "carry over. Sick leave is unlimited but absences over 3 consecutive days need a medical note. "
    "Unpaid leave is granted at HR's discretion. Requests should be raised at least 5 working days ahead.",
    "expenses": "Travel meals are reimbursed up to $50/day with itemised receipts. Claims must be filed "
    "within 30 days through a payroll ticket.",
    "benefits": "Health insurance covers the employee, spouse and up to two children from day one. "
    "There is a $500 yearly learning budget.",
    "holidays": "The company observes 10 public holidays per year; the calendar is published every January.",
    "code of conduct": "Treat colleagues with respect, protect confidential data and report concerns "
    "to HR or anonymously via the ethics line. Harassment of any kind is not tolerated.",
    "probation": "New hires have a 90-day probation period with check-ins at 30, 60 and 90 days.",
}
_ALIASES = {
    "work from home": "wfh",
    "remote": "wfh",
    "pto": "leave",
    "vacation": "leave",
    "sick": "leave",
    "travel": "expenses",
    "reimbursement": "expenses",
    "insurance": "benefits",
    "conduct": "code of conduct",
}


def get_policy(user: User, data: PolicyLookup) -> dict:
    topic = data.topic.lower()
    key = _ALIASES.get(topic, topic)
    if key not in POLICIES:
        match = get_close_matches(key, list(POLICIES) + list(_ALIASES), n=1, cutoff=0.6)
        key = _ALIASES.get(match[0], match[0]) if match else ""
    if not key:
        return {
            "topic": data.topic,
            "policy": None,
            "message": f"No policy found for '{data.topic}'. Available topics: {', '.join(POLICIES)}.",
        }
    text = POLICIES[key].format(annual=get_settings().annual_leave_days)
    return {"topic": key, "policy": text}


# --------------------------------------------------------------------------- analytics


def analytics(user: User) -> dict:
    _require_admin(user, "view organisation analytics")
    day = today().isoformat()
    with db.connect() as conn:

        def scalar(sql: str, params: tuple = ()) -> int:
            return int(conn.execute(sql, params).fetchone()[0] or 0)

        def pairs(sql: str) -> dict:
            return {r[0]: r[1] for r in conn.execute(sql).fetchall()}

        headcount = scalar("SELECT COUNT(*) FROM employees")
        result = {
            "headcount": headcount,
            "headcount_by_department": pairs(
                "SELECT department, COUNT(*) FROM employees GROUP BY department ORDER BY 2 DESC"
            ),
            "on_leave_today": scalar(
                "SELECT COUNT(DISTINCT employee_id) FROM leaves WHERE status = 'Approved' "
                "AND start_date <= ? AND end_date >= ?",
                (day, day),
            ),
            "pending_leave_requests": scalar("SELECT COUNT(*) FROM leaves WHERE status = 'Pending'"),
            "leaves_by_status": pairs("SELECT status, COUNT(*) FROM leaves GROUP BY status"),
            "leave_days_by_type": pairs(
                "SELECT leave_type, SUM(days) FROM leaves WHERE status = 'Approved' GROUP BY leave_type"
            ),
            "open_tickets": scalar("SELECT COUNT(*) FROM tickets WHERE status IN ('Open', 'In Progress')"),
            "open_high_priority_tickets": scalar(
                "SELECT COUNT(*) FROM tickets WHERE status IN ('Open', 'In Progress') AND priority = 'high'"
            ),
            "tickets_by_category": pairs("SELECT category, COUNT(*) FROM tickets GROUP BY category ORDER BY 2 DESC"),
            "tickets_by_status": pairs("SELECT status, COUNT(*) FROM tickets GROUP BY status"),
            "upcoming_interviews": scalar(
                "SELECT COUNT(*) FROM interviews WHERE status = 'Scheduled' AND scheduled_at >= ?", (now().isoformat(),)
            ),
            "average_review_rating": conn.execute("SELECT ROUND(AVG(rating), 2) FROM performance_reviews").fetchone()[
                0
            ],
        }
    return result


def audit_log(user: User, limit: int = 50) -> list[dict]:
    _require_admin(user, "view the audit log")
    return db.query("SELECT created_at, actor, role, action, details FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
