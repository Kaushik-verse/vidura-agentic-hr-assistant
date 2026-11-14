"""Pydantic models: the caller's identity and validated inputs for every HR operation.

The same models validate input from the chat agent's tool calls, the MCP server and the UI forms.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class Role(StrEnum):
    EMPLOYEE = "Employee"
    HR_ADMIN = "HR Admin"


class LeaveType(StrEnum):
    ANNUAL = "annual"
    SICK = "sick"
    UNPAID = "unpaid"


class LeaveStatus(StrEnum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    CANCELLED = "Cancelled"


class LeaveDecision(StrEnum):
    APPROVED = "Approved"
    REJECTED = "Rejected"


class TicketCategory(StrEnum):
    PAYROLL = "payroll"
    TECHNICAL = "technical"
    LEAVE = "leave"
    ONBOARDING = "onboarding"
    BENEFITS = "benefits"
    OTHER = "other"


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TicketStatus(StrEnum):
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    RESOLVED = "Resolved"
    CLOSED = "Closed"


class InterviewStatus(StrEnum):
    SCHEDULED = "Scheduled"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"
    NO_SHOW = "No-show"


class User(BaseModel):
    """The signed-in user. Every service call is checked against this, never against the prompt."""

    model_config = ConfigDict(frozen=True)

    name: Name
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role is Role.HR_ADMIN


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OnboardEmployee(_Input):
    name: Name = Field(description="Full name of the new hire.")
    job_title: ShortText = Field(description="Job title, e.g. 'UX Designer'.")
    department: ShortText = Field(description="Department, e.g. 'Engineering', 'Design', 'Sales'.")
    start_date: date | None = Field(default=None, description="First working day (YYYY-MM-DD). Defaults to today.")


class ApplyLeave(_Input):
    start_date: date = Field(description="First day of leave, ISO format YYYY-MM-DD.")
    days: int = Field(ge=1, le=30, description="Number of working days (Mon-Fri), 1-30.")
    reason: LongText = Field(description="Short reason for the leave.")
    leave_type: LeaveType = Field(default=LeaveType.ANNUAL, description="annual, sick or unpaid.")
    employee_name: Name | None = Field(
        default=None,
        description="Only for HR Admins filing on someone's behalf. Leave empty for the current user.",
    )


class EmployeeRef(_Input):
    employee_name: Name | None = Field(
        default=None, description="Employee to look up. Leave empty for the current user."
    )


class DecideLeave(_Input):
    leave_id: int = Field(ge=1, description="Leave request ID (look it up first, never guess).")
    decision: LeaveDecision = Field(description="'Approved' or 'Rejected'.")


class CancelLeave(_Input):
    leave_id: int = Field(ge=1, description="Leave request ID to cancel.")


class WhoIsOut(_Input):
    on_date: date | None = Field(default=None, description="Date to check (YYYY-MM-DD). Defaults to today.")
    department: ShortText | None = Field(default=None, description="Optional department filter.")


class CreateTicket(_Input):
    category: TicketCategory = Field(description="payroll, technical, leave, onboarding, benefits or other.")
    description: LongText = Field(description="What the problem is.")
    priority: TicketPriority = Field(default=TicketPriority.MEDIUM, description="low, medium or high.")


class ListTickets(_Input):
    status: TicketStatus | None = Field(default=None, description="Optional status filter.")


class UpdateTicket(_Input):
    ticket_id: int = Field(ge=1, description="Ticket ID (look it up first, never guess).")
    status: TicketStatus = Field(description="Open, In Progress, Resolved or Closed.")


class ScheduleInterview(_Input):
    candidate_name: Name
    position: ShortText = Field(description="Role the candidate is interviewing for.")
    interview_date: date = Field(description="Interview day, ISO format YYYY-MM-DD (a weekday).")
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="24-hour start time, HH:MM.")
    interviewer: Name = Field(description="Who conducts the interview.")

    @property
    def scheduled_at(self) -> datetime:
        return datetime.combine(self.interview_date, time.fromisoformat(self.start_time))


class UpdateInterview(_Input):
    interview_id: int = Field(ge=1)
    status: InterviewStatus


class SubmitReview(_Input):
    employee_name: Name
    rating: int = Field(ge=1, le=5, description="Rating from 1 (poor) to 5 (outstanding).")
    comments: LongText


class PolicyLookup(_Input):
    topic: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] = Field(
        description="Policy topic, e.g. 'wfh', 'leave', 'expenses', 'benefits', 'holidays', 'code of conduct'."
    )


class ListEmployees(_Input):
    department: ShortText | None = Field(default=None, description="Optional department filter.")


class NoInput(_Input):
    pass
