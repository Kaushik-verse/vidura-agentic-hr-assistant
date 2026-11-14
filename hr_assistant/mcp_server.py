"""Expose the HR tools over the Model Context Protocol (FastMCP).

Lets any MCP client (Claude Desktop, Cursor, an IDE agent...) use the same validated,
permission-checked HR operations as the chat UI.

The caller's identity comes from the environment, because MCP stdio servers run locally
on behalf of one user:

    HR_MCP_USER="Chitra Gupta" HR_MCP_ROLE="HR Admin" python -m hr_assistant.mcp_server
"""

from __future__ import annotations

import inspect
import logging
import os
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field, ValidationError

from . import services as svc
from .config import configure_logging
from .db import init_db
from .models import Role, User
from .tools import _SPECS, format_validation_error

log = logging.getLogger(__name__)


def current_user() -> User:
    return User(name=os.getenv("HR_MCP_USER", "Aditi Sharma"), role=Role(os.getenv("HR_MCP_ROLE", "Employee")))


def _register(mcp: FastMCP, user: User, name: str, description: str, model, fn) -> None:
    def handler(**kwargs):
        try:
            return fn(user, model(**kwargs))
        except ValidationError as exc:
            raise ToolError(f"Invalid input: {format_validation_error(exc)}") from exc
        except svc.HRError as exc:
            raise ToolError(str(exc)) from exc

    # Give the handler the input model's fields as its signature so MCP clients see a flat schema.
    annotations = {
        field: Annotated[(info.annotation, *info.metadata, Field(description=info.description))]
        for field, info in model.model_fields.items()
    }
    handler.__signature__ = inspect.Signature(
        [
            inspect.Parameter(
                field,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=annotations[field],
                default=inspect.Parameter.empty if info.is_required() else info.default,
            )
            for field, info in model.model_fields.items()
        ]
    )
    handler.__annotations__ = annotations
    handler.__name__ = name
    mcp.tool(handler, name=name, description=description)


def build_server(user: User | None = None) -> FastMCP:
    user = user or current_user()
    mcp = FastMCP("hr-assistant", instructions=f"HR operations acting as {user.name} ({user.role.value}).")
    for name, description, model, fn, admin_only in _SPECS:
        if user.is_admin or not admin_only:
            _register(mcp, user, name, description, model, fn)
    return mcp


def main() -> None:
    configure_logging()
    init_db()
    user = current_user()
    log.info("Starting MCP server as %s (%s)", user.name, user.role.value)
    build_server(user).run()


if __name__ == "__main__":
    main()
