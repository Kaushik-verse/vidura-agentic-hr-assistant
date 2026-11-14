"""Application settings, read once from environment variables (and an optional .env file)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    groq_api_key: str | None
    model: str
    db_path: Path
    seed_demo_data: bool
    annual_leave_days: int
    coverage_threshold: int
    log_level: str

    @property
    def llm_enabled(self) -> bool:
        return bool(self.groq_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    db_path = Path(os.getenv("HR_DB_PATH", "data/hr.db"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        model=os.getenv("HR_MODEL", "openai/gpt-oss-120b"),
        db_path=db_path,
        seed_demo_data=_bool(os.getenv("HR_SEED_DEMO_DATA"), True),
        annual_leave_days=_int("HR_ANNUAL_LEAVE_DAYS", 20, minimum=1),
        coverage_threshold=_int("HR_COVERAGE_THRESHOLD", 2, minimum=1),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


def configure_logging() -> None:
    """Idempotent logging setup shared by the UI, the MCP server and scripts."""
    root = logging.getLogger()
    if any(getattr(h, "_hr_assistant", False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler._hr_assistant = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(get_settings().log_level)
    # Third-party HTTP clients are noisy at INFO.
    for noisy in ("httpx", "httpcore", "groq"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
