from datetime import date

import pytest
from pydantic import ValidationError

from hr_assistant import db
from hr_assistant import services as svc
from hr_assistant.models import (
    ApplyLeave,
    CancelLeave,
    CreateTicket,
    DecideLeave,
    EmployeeRef,
    LeaveStatus,
    ListTickets,
    OnboardEmployee,
    PolicyLookup,
    Role,
    ScheduleInterview,
    SubmitReview,
    UpdateInterview,
    UpdateTicket,
    User,
    WhoIsOut,
)

# ----------------------------------------------------------------- onboarding


def test_onboarding_creates_employee_and_three_tickets(admin):
    result = svc.onboard_employee(
        admin, OnboardEmployee(name="Abhimanyu Rao", job_title="UX Designer", department="Design")
    )
    assert len(result["ticket_ids"]) == 3
    tickets = db.query("SELECT category FROM tickets WHERE employee_id = ?", (result["employee_id"],))
    assert sorted(t["category"] for t in tickets) == ["onboarding", "payroll", "technical"]
    assert any(r["name"] == "Abhimanyu Rao" for r in svc.onboarding_status(admin))


def test_onboarding_rejects_duplicates_case_insensitively(admin):
    with pytest.raises(svc.InvalidRequest, match="already exists"):
        svc.onboard_employee(admin, OnboardEmployee(name="aditi sharma", job_title="X Y", department="Design"))


def test_employee_cannot_onboard(aditi):
    with pytest.raises(svc.PermissionDenied):
        svc.onboard_employee(aditi, OnboardEmployee(name="Shakuni Gandhar", job_title="Admin", department="IT"))
    assert not db.query("SELECT 1 FROM employees WHERE name = 'Shakuni Gandhar'")


# ----------------------------------------------------------------- leave


def test_working_days_skip_weekends():
    assert svc.add_working_days(date(2026, 3, 5), 3) == date(2026, 3, 9)  # Thu, Fri, Mon
    assert svc.add_working_days(date(2026, 3, 2), 1) == date(2026, 3, 2)


def test_apply_leave_happy_path_and_balance(aditi):
    before = svc.leave_balance(aditi)["annual_remaining"]
    result = svc.apply_leave(aditi, ApplyLeave(start_date=date(2026, 3, 16), days=3, reason="Family visit"))
    assert result["end_date"] == "2026-03-18"
    assert result["coverage_warning"] is None
    assert svc.leave_balance(aditi)["annual_remaining"] == before - 3
    assert svc.leave_history(aditi)[0]["status"] == "Pending"


def test_coverage_warning_when_department_is_short(aditi):
    # Arjun (annual) and Gauri (sick) from Engineering are approved off this week.
    result = svc.apply_leave(aditi, ApplyLeave(start_date=date(2026, 3, 5), days=1, reason="Appointment"))
    assert result["coverage_warning"] and "Arjun Varma" in result["coverage_warning"]


def test_leave_validation(aditi):
    with pytest.raises(svc.InvalidRequest, match="past"):
        svc.apply_leave(aditi, ApplyLeave(start_date=date(2026, 3, 3), days=1, reason="Too late"))
    with pytest.raises(svc.InvalidRequest, match="weekend"):
        svc.apply_leave(aditi, ApplyLeave(start_date=date(2026, 3, 7), days=1, reason="Saturday"))
    with pytest.raises(ValidationError):
        ApplyLeave(start_date=date(2026, 3, 9), days=0, reason="Zero days")
    with pytest.raises(ValidationError):
        ApplyLeave(start_date="next monday", days=1, reason="Unparsed date")


def test_leave_overlap_and_balance_limits(aditi):
    svc.apply_leave(aditi, ApplyLeave(start_date=date(2026, 3, 16), days=2, reason="Trip"))
    with pytest.raises(svc.InvalidRequest, match="overlaps"):
        svc.apply_leave(aditi, ApplyLeave(start_date=date(2026, 3, 17), days=1, reason="Again"))
    with pytest.raises(svc.InvalidRequest, match="Not enough annual leave"):
        svc.apply_leave(aditi, ApplyLeave(start_date=date(2026, 4, 6), days=16, reason="Sabbatical"))
    # Sick and unpaid leave are not limited by the annual allowance.
    svc.apply_leave(aditi, ApplyLeave(start_date=date(2026, 4, 6), days=16, reason="Surgery", leave_type="sick"))


def test_employee_cannot_file_or_read_for_others(aditi):
    with pytest.raises(svc.PermissionDenied):
        svc.apply_leave(
            aditi, ApplyLeave(start_date=date(2026, 3, 16), days=1, reason="Spoof", employee_name="Arjun Varma")
        )
    with pytest.raises(svc.PermissionDenied):
        svc.leave_history(aditi, EmployeeRef(employee_name="Arjun Varma"))
    with pytest.raises(svc.PermissionDenied):
        svc.leave_balance(aditi, EmployeeRef(employee_name="Arjun Varma"))


def test_unknown_employee_gets_suggestion(admin):
    with pytest.raises(svc.NotFound, match="Did you mean 'Arjun Varma'"):
        svc.leave_history(admin, EmployeeRef(employee_name="Arjun Verma"))


def test_admin_decides_leave_once(admin, aditi):
    leave_id = svc.apply_leave(aditi, ApplyLeave(start_date=date(2026, 3, 16), days=1, reason="Errand"))["leave_id"]
    with pytest.raises(svc.PermissionDenied):
        svc.decide_leave(aditi, DecideLeave(leave_id=leave_id, decision="Approved"))
    svc.decide_leave(admin, DecideLeave(leave_id=leave_id, decision="Approved"))
    assert svc.leave_history(aditi)[0]["decided_by"] == "Chitra Gupta"
    with pytest.raises(svc.InvalidRequest, match="already Approved"):
        svc.decide_leave(admin, DecideLeave(leave_id=leave_id, decision="Rejected"))
    with pytest.raises(svc.NotFound):
        svc.decide_leave(admin, DecideLeave(leave_id=9999, decision="Approved"))


def test_admin_cannot_approve_own_leave(admin):
    leave_id = svc.apply_leave(admin, ApplyLeave(start_date=date(2026, 3, 16), days=1, reason="Errand"))["leave_id"]
    with pytest.raises(svc.PermissionDenied, match="own"):
        svc.decide_leave(admin, DecideLeave(leave_id=leave_id, decision="Approved"))


def test_cancel_leave_rules(aditi, arjun):
    leave_id = svc.apply_leave(aditi, ApplyLeave(start_date=date(2026, 3, 16), days=1, reason="Errand"))["leave_id"]
    with pytest.raises(svc.NotFound):  # Arjun can't see or cancel Aditi's request
        svc.cancel_leave(arjun, CancelLeave(leave_id=leave_id))
    svc.cancel_leave(aditi, CancelLeave(leave_id=leave_id))
    assert svc.leave_history(aditi)[0]["status"] == LeaveStatus.CANCELLED
    arjun_current = svc.leave_history(arjun)[0]["id"]  # approved leave that already started
    with pytest.raises(svc.InvalidRequest, match="already started"):
        svc.cancel_leave(arjun, CancelLeave(leave_id=arjun_current))


def test_who_is_out_hides_leave_type_from_employees(admin, aditi):
    out = svc.who_is_out(aditi, WhoIsOut(on_date=date(2026, 3, 4)))
    assert {r["name"] for r in out} == {"Arjun Varma", "Gauri Raman"}
    assert "leave_type" not in out[0]
    assert "leave_type" in svc.who_is_out(admin)[0]


# ----------------------------------------------------------------- tickets


def test_ticket_lifecycle(admin, aditi, arjun):
    ticket_id = svc.create_ticket(
        arjun, CreateTicket(category="payroll", description="Salary is late", priority="high")
    )["ticket_id"]
    assert [t["id"] for t in svc.list_tickets(arjun)] == [ticket_id]
    assert ticket_id not in [t["id"] for t in svc.list_tickets(aditi)]
    assert ticket_id in [t["id"] for t in svc.list_tickets(admin)]

    with pytest.raises(svc.PermissionDenied):
        svc.update_ticket(arjun, UpdateTicket(ticket_id=ticket_id, status="Resolved"))
    svc.update_ticket(admin, UpdateTicket(ticket_id=ticket_id, status="Closed"))
    with pytest.raises(svc.InvalidRequest, match="closed"):
        svc.update_ticket(admin, UpdateTicket(ticket_id=ticket_id, status="Open"))
    assert svc.list_tickets(arjun, ListTickets(status="Closed"))[0]["id"] == ticket_id


def test_ticket_input_is_validated():
    with pytest.raises(ValidationError):
        CreateTicket(category="hardware", description="Bad category")
    with pytest.raises(ValidationError):
        UpdateTicket(ticket_id="abc", status="Resolved")
    with pytest.raises(ValidationError):
        UpdateTicket(ticket_id=1, status="Done")


def test_missing_ticket(admin):
    with pytest.raises(svc.NotFound):
        svc.update_ticket(admin, UpdateTicket(ticket_id=999, status="Resolved"))


# ----------------------------------------------------------------- interviews


def test_schedule_interview_and_clash_detection(admin, aditi):
    data = ScheduleInterview(
        candidate_name="Nandini Patel",
        position="Data Analyst",
        interview_date=date(2026, 3, 12),
        start_time="15:00",
        interviewer="Aditi Sharma",
    )
    interview_id = svc.schedule_interview(admin, data)["interview_id"]
    with pytest.raises(svc.InvalidRequest, match="already has an interview"):
        svc.schedule_interview(admin, data.model_copy(update={"candidate_name": "Om Prakash", "start_time": "15:30"}))
    with pytest.raises(svc.InvalidRequest, match="future"):
        svc.schedule_interview(admin, data.model_copy(update={"interview_date": date(2026, 3, 2)}))
    with pytest.raises(svc.InvalidRequest, match="weekend"):
        svc.schedule_interview(admin, data.model_copy(update={"interview_date": date(2026, 3, 14)}))
    with pytest.raises(ValidationError):
        ScheduleInterview(
            candidate_name="Nandini Patel",
            position="Analyst",
            interview_date=date(2026, 3, 12),
            start_time="3pm",
            interviewer="Aditi Sharma",
        )
    with pytest.raises(svc.PermissionDenied):
        svc.schedule_interview(aditi, data)

    assert interview_id in [i["id"] for i in svc.list_interviews(admin)]
    svc.update_interview(admin, UpdateInterview(interview_id=interview_id, status="Cancelled"))
    assert interview_id not in [i["id"] for i in svc.list_interviews(admin)]


# ----------------------------------------------------------------- reviews


def test_reviews(admin, aditi, arjun):
    svc.submit_review(admin, SubmitReview(employee_name="Arjun Varma", rating=5, comments="Great quarter."))
    assert svc.list_reviews(arjun)[0]["rating"] == 5
    with pytest.raises(svc.PermissionDenied):
        svc.list_reviews(aditi, EmployeeRef(employee_name="Arjun Varma"))
    with pytest.raises(svc.PermissionDenied):
        svc.submit_review(aditi, SubmitReview(employee_name="Arjun Varma", rating=1, comments="Retaliation"))
    with pytest.raises(svc.PermissionDenied, match="yourself"):
        svc.submit_review(admin, SubmitReview(employee_name="Chitra Gupta", rating=5, comments="I'm great"))
    with pytest.raises(ValidationError):
        SubmitReview(employee_name="Arjun Varma", rating=6, comments="Off the charts")


# ----------------------------------------------------------------- policy, analytics, audit


def test_policy_lookup_handles_aliases_and_typos(aditi):
    assert svc.get_policy(aditi, PolicyLookup(topic="work from home"))["topic"] == "wfh"
    assert svc.get_policy(aditi, PolicyLookup(topic="expences"))["topic"] == "expenses"
    assert "20 days" in svc.get_policy(aditi, PolicyLookup(topic="PTO"))["policy"]
    missing = svc.get_policy(aditi, PolicyLookup(topic="parking"))
    assert missing["policy"] is None and "Available topics" in missing["message"]


def test_analytics_and_audit_are_admin_only(admin, aditi):
    stats = svc.analytics(admin)
    assert stats["headcount"] == 9
    assert stats["on_leave_today"] == 2
    assert stats["pending_leave_requests"] == 2
    with pytest.raises(svc.PermissionDenied):
        svc.analytics(aditi)
    with pytest.raises(svc.PermissionDenied):
        svc.audit_log(aditi)


def test_actions_are_audited(admin):
    svc.create_ticket(admin, CreateTicket(category="other", description="Order more chairs"))
    entry = svc.audit_log(admin)[0]
    assert entry["actor"] == "Chitra Gupta" and entry["action"] == "create_ticket"


def test_unregistered_employee_is_rejected():
    ghost = User(name="Ghost User", role=Role.EMPLOYEE)
    with pytest.raises(svc.NotFound, match="not on the employee register"):
        svc.leave_balance(ghost)


def test_reset_restores_demo_data(admin):
    svc.onboard_employee(admin, OnboardEmployee(name="Temp Person", job_title="Intern", department="Sales"))
    db.reset_db()
    assert svc.analytics(admin)["headcount"] == 9


def test_onboarding_progress_ignores_ordinary_tickets(admin):
    names = {r["name"] for r in svc.onboarding_status(admin)}
    assert names == {"Nakul Mehta"}  # Aditi and Maya only have their own support tickets
