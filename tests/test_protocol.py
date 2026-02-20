"""
Protocol unit tests — no Blender required, no network required.
Tests message serialization, version checking, error codes, and parsing.
"""

import json
import pytest

from blender_mcp.protocol import (
    PROTOCOL_VERSION,
    Command,
    ErrorCode,
    ErrorDetail,
    Request,
    Response,
    make_error_response,
    make_success_response,
    parse_request,
    parse_response,
    versions_compatible,
)


# ─── Version Compatibility ────────────────────────────────────────────────────

class TestVersionCompatibility:
    def test_same_version_compatible(self):
        assert versions_compatible("0.1.0", "0.1.0")

    def test_different_patch_compatible(self):
        assert versions_compatible("0.1.0", "0.1.5")
        assert versions_compatible("0.1.5", "0.1.0")

    def test_different_minor_incompatible(self):
        assert not versions_compatible("0.1.0", "0.2.0")
        assert not versions_compatible("0.2.0", "0.1.0")

    def test_different_major_incompatible(self):
        assert not versions_compatible("1.0.0", "2.0.0")

    def test_current_version_self_compatible(self):
        assert versions_compatible(PROTOCOL_VERSION, PROTOCOL_VERSION)


# ─── Request Serialization ────────────────────────────────────────────────────

class TestRequest:
    def test_to_json_round_trip(self):
        req = Request(command=Command.PING)
        raw = req.to_json()
        assert raw.endswith("\n")
        d = json.loads(raw)
        assert d["command"] == Command.PING
        assert d["protocol_version"] == PROTOCOL_VERSION
        assert "message_id" in d

    def test_params_included(self):
        req = Request(command=Command.EXECUTE_CODE, params={"code": "print(1)"})
        d = json.loads(req.to_json())
        assert d["params"]["code"] == "print(1)"

    def test_message_id_unique(self):
        ids = {Request(command=Command.PING).message_id for _ in range(100)}
        assert len(ids) == 100

    def test_from_dict_round_trip(self):
        req = Request(command=Command.GET_SCENE_INFO, params={"detail_level": "full"})
        d = json.loads(req.to_json())
        req2 = Request.from_dict(d)
        assert req2.command == req.command
        assert req2.params == req.params
        assert req2.message_id == req.message_id


# ─── Response Serialization ───────────────────────────────────────────────────

class TestResponse:
    def test_success_response(self):
        resp = make_success_response("abc-123", {"foo": "bar"})
        assert resp.is_success
        raw = resp.to_json()
        d = json.loads(raw)
        assert d["status"] == "success"
        assert d["result"]["foo"] == "bar"
        assert "error" not in d

    def test_error_response(self):
        resp = make_error_response("abc-123", ErrorCode.EXECUTION_ERROR, "NameError: x")
        assert not resp.is_success
        d = json.loads(resp.to_json())
        assert d["status"] == "error"
        assert d["error"]["code"] == ErrorCode.EXECUTION_ERROR
        assert d["error"]["message"] == "NameError: x"
        assert "result" not in d

    def test_error_with_traceback(self):
        resp = make_error_response("id", ErrorCode.EXECUTION_ERROR, "oops", traceback="Traceback...")
        d = json.loads(resp.to_json())
        assert d["error"]["traceback"] == "Traceback..."

    def test_error_without_traceback_no_key(self):
        resp = make_error_response("id", ErrorCode.INTERNAL_ERROR, "whoops")
        d = json.loads(resp.to_json())
        assert "traceback" not in d["error"]

    def test_from_dict_success(self):
        raw = json.dumps({
            "protocol_version": PROTOCOL_VERSION,
            "message_id": "xyz",
            "status": "success",
            "result": {"k": "v"},
        })
        resp, err = parse_response(raw)
        assert err is None
        assert resp is not None
        assert resp.is_success
        assert resp.result == {"k": "v"}

    def test_from_dict_error(self):
        raw = json.dumps({
            "protocol_version": PROTOCOL_VERSION,
            "message_id": "xyz",
            "status": "error",
            "error": {"code": "TIMEOUT", "message": "timed out", "traceback": "..."},
        })
        resp, err = parse_response(raw)
        assert err is None
        assert resp is not None
        assert not resp.is_success
        assert resp.error.code == "TIMEOUT"
        assert resp.error.traceback == "..."

    def test_protocol_version_in_response(self):
        resp = make_success_response("id", {})
        d = json.loads(resp.to_json())
        assert d["protocol_version"] == PROTOCOL_VERSION


# ─── Parse Request ────────────────────────────────────────────────────────────

class TestParseRequest:
    def test_valid_ping(self):
        raw = json.dumps({
            "protocol_version": PROTOCOL_VERSION,
            "message_id": "abc",
            "command": Command.PING,
            "params": {},
        })
        req, err = parse_request(raw)
        assert err is None
        assert req is not None
        assert req.command == Command.PING

    def test_invalid_json(self):
        req, err = parse_request("not json{")
        assert req is None
        assert "Invalid JSON" in err

    def test_missing_command(self):
        req, err = parse_request(json.dumps({"params": {}}))
        assert req is None
        assert "Missing" in err

    def test_unknown_command(self):
        req, err = parse_request(json.dumps({"command": "do_magic", "params": {}}))
        assert req is None
        assert "Unknown command" in err

    def test_all_valid_commands_accepted(self):
        for cmd in [
            Command.EXECUTE_CODE, Command.GET_SCENE_INFO, Command.EXPORT_MESH,
            Command.CHECK_PRINTABILITY, Command.SCREENSHOT, Command.IMPORT_MESH,
            Command.PING, Command.GET_VERSION,
        ]:
            raw = json.dumps({"command": cmd, "params": {}})
            req, err = parse_request(raw)
            assert err is None, f"Command {cmd} rejected: {err}"
            assert req is not None


# ─── Parse Response ───────────────────────────────────────────────────────────

class TestParseResponse:
    def test_invalid_json(self):
        resp, err = parse_response("{bad}")
        assert resp is None
        assert "Invalid JSON" in err

    def test_missing_status(self):
        resp, err = parse_response(json.dumps({"result": {}}))
        assert resp is None
        assert "Missing" in err

    def test_whitespace_stripped(self):
        raw = json.dumps({"status": "success", "message_id": "x", "result": {}}) + "\n"
        resp, err = parse_response(raw)
        assert err is None
        assert resp is not None


# ─── Error Codes ─────────────────────────────────────────────────────────────

class TestErrorCodes:
    def test_all_error_codes_are_strings(self):
        codes = [
            ErrorCode.EXECUTION_ERROR,
            ErrorCode.TIMEOUT,
            ErrorCode.INVALID_COMMAND,
            ErrorCode.INVALID_PARAMS,
            ErrorCode.OBJECT_NOT_FOUND,
            ErrorCode.EXPORT_FAILED,
            ErrorCode.IMPORT_FAILED,
            ErrorCode.VERSION_MISMATCH,
            ErrorCode.INTERNAL_ERROR,
            ErrorCode.CONNECTION_REFUSED,
            ErrorCode.CONNECTION_LOST,
            ErrorCode.CONNECTION_TIMEOUT,
        ]
        for code in codes:
            assert isinstance(code, str), f"Error code {code!r} is not a string"

    def test_error_codes_unique(self):
        codes = [
            ErrorCode.EXECUTION_ERROR, ErrorCode.TIMEOUT, ErrorCode.INVALID_COMMAND,
            ErrorCode.INVALID_PARAMS, ErrorCode.OBJECT_NOT_FOUND, ErrorCode.EXPORT_FAILED,
            ErrorCode.IMPORT_FAILED, ErrorCode.VERSION_MISMATCH, ErrorCode.INTERNAL_ERROR,
        ]
        assert len(codes) == len(set(codes)), "Duplicate error codes"
