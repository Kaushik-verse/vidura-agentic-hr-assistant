from datetime import date, datetime

import pytest

from hr_assistant import config, services
from hr_assistant.db import init_db
from hr_assistant.models import Role, User

TODAY = date(2026, 3, 4)  # a Wednesday


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets a fresh, seeded database and a frozen clock."""
    monkeypatch.setenv("HR_DB_PATH", str(tmp_path / "hr.db"))
    monkeypatch.setenv("HR_SEED_DEMO_DATA", "true")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    config.get_settings.cache_clear()
    monkeypatch.setattr(services, "today", lambda: TODAY)
    monkeypatch.setattr(services, "now", lambda: datetime(2026, 3, 4, 9, 0))
    init_db()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def admin() -> User:
    return User(name="Chitra Gupta", role=Role.HR_ADMIN)


@pytest.fixture
def aditi() -> User:
    return User(name="Aditi Sharma", role=Role.EMPLOYEE)


@pytest.fixture
def arjun() -> User:
    return User(name="Arjun Varma", role=Role.EMPLOYEE)
