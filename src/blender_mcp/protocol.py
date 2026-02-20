"""
Blender MCP — Protocol Definitions

Shared protocol between the MCP server and the Blender addon.
Version carried in every message for compatibility checking.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

# ─── Version ────────────────────────────────────────────────────────────────

PROTOCOL_VERSION = "0.1.0"


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def versions_compatible(a: str, b: str) -> bool:
    """Major and minor must match; patch may differ."""
    ta, tb = _version_tuple(a), _version_tuple(b)
    return ta[:2] == tb[:2]


# ─── Error Codes ─────────────────────────────────────────────────────────────

class ErrorCode:
    EXECUTION_ERROR   = "EXECUTION_ERROR"
    TIMEOUT           = "TIMEOUT"
    INVALID_COMMAND   = "INVALID_COMMAND"
    INVALID_PARAMS    = "INVALID_PARAMS"
    OBJECT_NOT_FOUND  = "OBJECT_NOT_FOUND"
    EXPORT_FAILED     = "EXPORT_FAILED"
    IMPORT_FAILED     = "IMPORT_FAILED"
    VERSION_MISMATCH  = "VERSION_MISMATCH"
    INTERNAL_ERROR    = "INTERNAL_ERROR"
    # Connection-level codes (MCP server only, never sent over wire)
    CONNECTION_REFUSED  = "CONNECTION_REFUSED"
    CONNECTION_LOST     = "CONNECTION_LOST"
    CONNECTION_TIMEOUT  = "CONNECTION_TIMEOUT"


# ─── Commands ────────────────────────────────────────────────────────────────

class Command:
    EXECUTE_CODE      = "execute_code"
    GET_SCENE_INFO    = "get_scene_info"
    EXPORT_MESH       = "export_mesh"
    CHECK_PRINTABILITY = "check_printability"
    SCREENSHOT        = "screenshot"
    IMPORT_MESH       = "import_mesh"
    PING              = "ping"
    GET_VERSION       = "get_version"


VALID_COMMANDS = {
    Command.EXECUTE_CODE,
    Command.GET_SCENE_INFO,
    Command.EXPORT_MESH,
    Command.CHECK_PRINTABILITY,
    Command.SCREENSHOT,
    Command.IMPORT_MESH,
    Command.PING,
    Command.GET_VERSION,
}


# ─── Message Dataclasses ─────────────────────────────────────────────────────

@dataclass
class Request:
    command: str
    params: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    protocol_version: str = PROTOCOL_VERSION

    def to_json(self) -> str:
        return json.dumps({
            "protocol_version": self.protocol_version,
            "message_id": self.message_id,
            "command": self.command,
            "params": self.params,
        }) + "\n"

    @classmethod
    def from_dict(cls, d: dict) -> "Request":
        return cls(
            command=d["command"],
            params=d.get("params", {}),
            message_id=d.get("message_id", str(uuid.uuid4())),
            protocol_version=d.get("protocol_version", PROTOCOL_VERSION),
        )


@dataclass
class ErrorDetail:
    code: str
    message: str
    traceback: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.traceback:
            d["traceback"] = self.traceback
        if self.context:
            d["context"] = self.context
        return d


@dataclass
class Response:
    message_id: str
    status: str               # "success" | "error"
    result: dict[str, Any] | None = None
    error: ErrorDetail | None = None
    protocol_version: str = PROTOCOL_VERSION

    def to_json(self) -> str:
        d: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "message_id": self.message_id,
            "status": self.status,
        }
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error.to_dict()
        return json.dumps(d) + "\n"

    @classmethod
    def from_dict(cls, d: dict) -> "Response":
        error = None
        if "error" in d and d["error"]:
            e = d["error"]
            error = ErrorDetail(
                code=e.get("code", ErrorCode.INTERNAL_ERROR),
                message=e.get("message", "Unknown error"),
                traceback=e.get("traceback"),
                context=e.get("context", {}),
            )
        return cls(
            message_id=d.get("message_id", ""),
            status=d.get("status", "error"),
            result=d.get("result"),
            error=error,
            protocol_version=d.get("protocol_version", PROTOCOL_VERSION),
        )

    @property
    def is_success(self) -> bool:
        return self.status == "success"


# ─── Convenience Constructors ────────────────────────────────────────────────

def make_success_response(message_id: str, result: dict[str, Any]) -> Response:
    return Response(message_id=message_id, status="success", result=result)


def make_error_response(
    message_id: str,
    code: str,
    message: str,
    traceback: str | None = None,
    context: dict[str, Any] | None = None,
) -> Response:
    return Response(
        message_id=message_id,
        status="error",
        error=ErrorDetail(
            code=code,
            message=message,
            traceback=traceback,
            context=context or {},
        ),
    )


# ─── Parsing ─────────────────────────────────────────────────────────────────

def parse_request(raw: str) -> tuple[Request | None, str | None]:
    """Parse a JSON string into a Request. Returns (request, error_message)."""
    try:
        d = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"

    if "command" not in d:
        return None, "Missing required field: command"

    if d["command"] not in VALID_COMMANDS:
        return None, f"Unknown command: {d['command']!r}"

    try:
        req = Request.from_dict(d)
    except (KeyError, TypeError) as e:
        return None, f"Malformed request: {e}"

    return req, None


def parse_response(raw: str) -> tuple[Response | None, str | None]:
    """Parse a JSON string into a Response. Returns (response, error_message)."""
    try:
        d = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"

    if "status" not in d:
        return None, "Missing required field: status"

    try:
        resp = Response.from_dict(d)
    except (KeyError, TypeError) as e:
        return None, f"Malformed response: {e}"

    return resp, None
