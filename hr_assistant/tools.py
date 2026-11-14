"""LangChain tools for the chat agent.

Tools are built per request and bound to the signed-in user, so the model can never choose
who it acts as. Admin-only tools are not even offered to employees, and the service layer
re-checks the role anyway.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ValidationError

from . import services as svc
from .models import (
    ApplyLeave,
    CancelLeave,
    CreateTicket,
    DecideLeave,
    EmployeeRef,
    ListEmployees,
    ListTickets,
    NoInput,
    OnboardEmployee,
    PolicyLookup,
    ScheduleInterview,
    SubmitReview,
    UpdateInterview,
    UpdateTicket,
    User,
    WhoIsOut,
)

log = logging.getLogger(__name__)


def format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "input"
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts)


def run_safely(fn: Callable[[], Any]) -> str:
    """Run a service call and turn the outcome into text the model can act on."""
    try:
        result = fn()
    except ValidationError as exc:
        return f"Error: invalid input ({format_validation_error(exc)}). Fix the arguments and try again."
    except svc.HRError as exc:
        return f"Error: {exc}"
    except Exception:
        log.exception("Tool failed unexpectedly")
        return "Error: an internal error occurred. Tell the user to try again later."
    if isinstance(result, list) and not result:
        return "No records found."
    return json.dumps(result, default=str, ensure_ascii=False)


# (name, description, input model, service function, admin only)
_SPECS: list[tuple[str, str, type[BaseModel], Callable[..., Any], bool]] = [
    (
        "get_company_policy",
        "Look up a company policy (WFH, leave, expenses, benefits, holidays, conduct, probation).",
        PolicyLookup,
        svc.get_policy,
        False,
    ),
    (
        "list_employees",
        "Company directory: names, job titles and departments.",
        ListEmployees,
        svc.list_employees,
        False,
    ),
    (
        "apply_leave",
        "Submit a leave request. Checks balance, overlaps and team coverage.",
        ApplyLeave,
        svc.apply_leave,
        False,
    ),
    (
        "get_leave_balance",
        "Annual leave allowance, used and remaining days for this year.",
        EmployeeRef,
        svc.leave_balance,
        False,
    ),
    ("get_leave_history", "List leave requests (with IDs) for an employee.", EmployeeRef, svc.leave_history, False),
    (
        "cancel_leave",
        "Cancel a pending or approved leave request that has not started yet.",
        CancelLeave,
        svc.cancel_leave,
        False,
    ),
    ("who_is_out", "Who is on approved leave on a given date.", WhoIsOut, svc.who_is_out, False),
    ("create_ticket", "Open an HR support ticket for the current user.", CreateTicket, svc.create_ticket, False),
    (
        "list_tickets",
        "List support tickets with IDs (employees see only their own).",
        ListTickets,
        svc.list_tickets,
        False,
    ),
    ("get_performance_reviews", "Performance reviews for an employee.", EmployeeRef, svc.list_reviews, False),
    # Admin only
    (
        "onboard_employee",
        "Onboard a new hire: creates the employee plus IT, payroll and orientation tickets.",
        OnboardEmployee,
        svc.onboard_employee,
        True,
    ),
    ("decide_leave", "Approve or reject a pending leave request by ID.", DecideLeave, svc.decide_leave, True),
    ("update_ticket_status", "Change a ticket's status by ID.", UpdateTicket, svc.update_ticket, True),
    ("schedule_interview", "Book a candidate interview.", ScheduleInterview, svc.schedule_interview, True),
    (
        "update_interview_status",
        "Mark an interview Completed, Cancelled or No-show.",
        UpdateInterview,
        svc.update_interview,
        True,
    ),
    (
        "list_upcoming_interviews",
        "Upcoming scheduled interviews.",
        NoInput,
        lambda user, _: svc.list_interviews(user),
        True,
    ),
    ("submit_performance_review", "Record a performance review (rating 1-5).", SubmitReview, svc.submit_review, True),
    (
        "get_hr_analytics",
        "Organisation metrics: headcount, leave, tickets, interviews, ratings.",
        NoInput,
        lambda user, _: svc.analytics(user),
        True,
    ),
    (
        "get_onboarding_status",
        "Onboarding task progress for new hires.",
        NoInput,
        lambda user, _: svc.onboarding_status(user),
        True,
    ),
]


def _make_tool(user: User, name: str, description: str, model: type[BaseModel], fn: Callable) -> BaseTool:
    def call(**kwargs: Any) -> str:
        return run_safely(lambda: fn(user, model(**kwargs)))

    return StructuredTool.from_function(
        func=call,
        name=name,
        description=description,
        args_schema=model,
        handle_validation_error=lambda exc: (
            f"Error: invalid input ({format_validation_error(exc)}). Fix the arguments and try again."
            if isinstance(exc, ValidationError)
            else f"Error: invalid input ({exc}). Fix the arguments and try again."
        ),
    )


def build_tools(user: User) -> list[BaseTool]:
    return [
        _make_tool(user, name, desc, model, fn)
        for name, desc, model, fn, admin_only in _SPECS
        if user.is_admin or not admin_only
    ]


ADMIN_TOOL_NAMES = frozenset(name for name, *_, admin in _SPECS if admin)
