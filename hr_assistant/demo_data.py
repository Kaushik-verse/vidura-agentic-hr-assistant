"""Small, realistic demo dataset so the dashboards and agent have something to work with.

Dates are relative to today so the demo always looks current.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import db, services
from .services import add_working_days

EMPLOYEES = [
    ("Aditi Sharma", "Data Scientist", "Engineering", 420),
    ("Arjun Varma", "Backend Engineer", "Engineering", 900),
    ("Gauri Raman", "ML Engineer", "Engineering", 300),
    ("Nakul Mehta", "Frontend Engineer", "Engineering", 12),
    ("Vishwa Kapoor", "Product Designer", "Design", 640),
    ("Maya Sen", "UX Researcher", "Design", 150),
    ("Lakshmi Pillai", "Account Executive", "Sales", 510),
    ("Kuber Bhatia", "Sales Manager", "Sales", 1200),
    ("Chitra Gupta", "HR Business Partner", "People Ops", 800),
]


def _monday_on_or_before(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _working_day_on_or_after(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def seed() -> None:
    t = services.today()
    # On a weekend, treat the coming week as "this week" so someone is always out on the next working day.
    this_week = _monday_on_or_before(_working_day_on_or_after(t))
    next_week = this_week + timedelta(days=7)

    with db.connect() as conn:
        ids = {}
        for name, title, dept, tenure_days in EMPLOYEES:
            cur = conn.execute(
                "INSERT INTO employees (name, job_title, department, start_date) VALUES (?, ?, ?, ?)",
                (name, title, dept, (t - timedelta(days=tenure_days)).isoformat()),
            )
            ids[name] = cur.lastrowid

        def leave(name, ltype, start, days, reason, status, decided_by=None):
            end = add_working_days(start, days)
            conn.execute(
                "INSERT INTO leaves (employee_id, leave_type, start_date, end_date, days, reason, status, decided_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ids[name], ltype, start.isoformat(), end.isoformat(), days, reason, status, decided_by),
            )

        leave("Arjun Varma", "annual", this_week, 5, "Family trip", "Approved", "Chitra Gupta")
        leave("Gauri Raman", "sick", _working_day_on_or_after(t), 2, "Flu", "Approved", "Chitra Gupta")
        leave("Aditi Sharma", "annual", this_week - timedelta(days=42), 3, "Long weekend", "Approved", "Chitra Gupta")
        leave("Vishwa Kapoor", "annual", next_week + timedelta(days=7), 4, "Wedding", "Pending")
        leave("Lakshmi Pillai", "annual", next_week, 2, "Moving house", "Pending")
        leave(
            "Kuber Bhatia", "unpaid", this_week - timedelta(days=21), 3, "Personal matters", "Rejected", "Chitra Gupta"
        )

        tickets = [
            (
                "Aditi Sharma",
                "Aditi Sharma",
                "technical",
                "high",
                "Laptop fan is failing and the machine overheats.",
                "In Progress",
            ),
            (
                "Aditi Sharma",
                "Aditi Sharma",
                "payroll",
                "medium",
                "Last month's overtime is missing from my payslip.",
                "Open",
            ),
            (
                "Lakshmi Pillai",
                "Lakshmi Pillai",
                "benefits",
                "low",
                "How do I add my spouse to health insurance?",
                "Resolved",
            ),
            (
                "Maya Sen",
                "Maya Sen",
                "technical",
                "medium",
                "Need a licence for the usability testing tool.",
                "Open",
            ),
            (
                "Chitra Gupta",
                "Nakul Mehta",
                "technical",
                "high",
                "Provision laptop, email and system access for Nakul Mehta (Frontend Engineer).",
                "Resolved",
            ),
            (
                "Chitra Gupta",
                "Nakul Mehta",
                "payroll",
                "medium",
                "Set up direct deposit and tax forms for Nakul Mehta.",
                "Open",
            ),
            (
                "Chitra Gupta",
                "Nakul Mehta",
                "onboarding",
                "medium",
                "Schedule orientation and assign a buddy for Nakul Mehta in Engineering.",
                "In Progress",
            ),
        ]
        for created_by, subject, category, priority, description, status in tickets:
            conn.execute(
                "INSERT INTO tickets (created_by, employee_id, category, priority, description, status, "
                "onboarding_task) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (created_by, ids[subject], category, priority, description, status, int(created_by != subject)),
            )

        def at(day: date, hour: int) -> str:
            return datetime(day.year, day.month, day.day, hour).isoformat()

        interviews = [
            (
                "Savitri Menon",
                "Senior Data Engineer",
                "Arjun Varma",
                at(next_week + timedelta(days=1), 10),
                "Scheduled",
            ),
            ("Dhruv Kulkarni", "Product Designer", "Vishwa Kapoor", at(next_week + timedelta(days=2), 14), "Scheduled"),
            ("Harini Das", "Account Executive", "Kuber Bhatia", at(this_week - timedelta(days=5), 11), "Completed"),
        ]
        conn.executemany(
            "INSERT INTO interviews (candidate_name, position, interviewer, scheduled_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            interviews,
        )

        reviews = [
            ("Aditi Sharma", 5, "Shipped the churn model ahead of schedule; clear communicator."),
            ("Arjun Varma", 4, "Reliable owner of the payments service. Should mentor more."),
            ("Lakshmi Pillai", 4, "Exceeded quarterly quota by 12%."),
        ]
        for name, rating, comments in reviews:
            conn.execute(
                "INSERT INTO performance_reviews (employee_id, reviewer, rating, comments) VALUES (?, ?, ?, ?)",
                (ids[name], "Chitra Gupta", rating, comments),
            )
