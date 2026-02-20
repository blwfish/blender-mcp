"""
Addon unit tests — exercises blender_mcp_bridge without a running Blender.
Uses sys.modules stubs to satisfy the bpy/bmesh imports at load time.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ADDON_PATH = Path(__file__).parent.parent / "addon" / "blender_mcp_bridge.py"


@pytest.fixture(scope="module")
def addon():
    """Load the addon module with bpy/bmesh stubbed out."""
    bpy_stub = MagicMock()
    bpy_stub.app.version_string = "5.0.1-test"

    saved = {n: sys.modules.pop(n, None) for n in ("bpy", "bmesh")}
    sys.modules["bpy"] = bpy_stub
    sys.modules["bmesh"] = MagicMock()
    sys.modules.pop("blender_mcp_bridge", None)

    spec = importlib.util.spec_from_file_location("blender_mcp_bridge", ADDON_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["blender_mcp_bridge"] = mod
    spec.loader.exec_module(mod)

    yield mod

    sys.modules.pop("blender_mcp_bridge", None)
    for name, old in saved.items():
        if old is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old


class TestPendingCommand:
    """
    Regression tests for _PendingCommand.

    Bug: _response was missing from __slots__, causing AttributeError the moment
    any client connected. The exception propagated through ClientConnection._run's
    finally block, silently closing the socket. The MCP server saw an empty readline
    and raised CONNECTION_LOST. Fix: add "_response" to __slots__.
    """

    def test_slots_includes_response(self, addon):
        """Explicit guard: _response must be in __slots__ or instantiation fails."""
        assert "_response" in addon._PendingCommand.__slots__

    def test_can_instantiate(self, addon):
        """Regression: AttributeError on __init__ caused silent connection drop."""
        pending = addon._PendingCommand({"command": "ping"}, MagicMock())
        assert pending._response is None

    def test_set_and_get_response(self, addon):
        pending = addon._PendingCommand({"command": "ping"}, MagicMock())
        data = b'{"status": "success"}\n'
        pending.set_response(data)
        assert pending.get_response(timeout=1.0) == data

    def test_done_event_set_after_response(self, addon):
        pending = addon._PendingCommand({"command": "ping"}, MagicMock())
        assert not pending.done.is_set()
        pending.set_response(b"test")
        assert pending.done.is_set()

    def test_get_response_timeout_returns_none(self, addon):
        pending = addon._PendingCommand({"command": "ping"}, MagicMock())
        # Never call set_response — should time out and return None
        assert pending.get_response(timeout=0.05) is None


class TestAddonLogger:
    """Addon-side file logging."""

    def test_log_file_created(self, addon, tmp_path, monkeypatch):
        monkeypatch.setenv("BLENDERMCP_LOG_DIR", str(tmp_path))
        addon._setup_logger()
        assert (tmp_path / "addon.log").exists()

    def test_logger_has_file_handler(self, addon, tmp_path, monkeypatch):
        import logging
        monkeypatch.setenv("BLENDERMCP_LOG_DIR", str(tmp_path))
        log = addon._setup_logger()
        from logging.handlers import RotatingFileHandler
        assert any(isinstance(h, RotatingFileHandler) for h in log.handlers)

    def test_error_written_to_file(self, addon, tmp_path, monkeypatch):
        monkeypatch.setenv("BLENDERMCP_LOG_DIR", str(tmp_path))
        log = addon._setup_logger()
        log.error("test error sentinel XYZ")
        # flush handlers
        for h in log.handlers:
            h.flush()
        content = (tmp_path / "addon.log").read_text()
        assert "test error sentinel XYZ" in content


class TestMessageHelpers:
    """Smoke-test the pure-Python message helpers."""

    def test_make_success_is_valid_json(self, addon):
        import json
        raw = addon._make_success("msg-1", {"foo": "bar"})
        assert raw.endswith(b"\n")
        d = json.loads(raw)
        assert d["status"] == "success"
        assert d["result"]["foo"] == "bar"

    def test_make_error_is_valid_json(self, addon):
        import json
        raw = addon._make_error("msg-2", "SOME_CODE", "something went wrong")
        assert raw.endswith(b"\n")
        d = json.loads(raw)
        assert d["status"] == "error"
        assert d["error"]["code"] == "SOME_CODE"

    def test_parse_request_valid(self, addon):
        req, err = addon._parse_request('{"command": "ping"}')
        assert err == ""
        assert req["command"] == "ping"

    def test_parse_request_missing_command(self, addon):
        req, err = addon._parse_request('{"foo": "bar"}')
        assert req is None
        assert "command" in err

    def test_parse_request_invalid_json(self, addon):
        req, err = addon._parse_request("not json")
        assert req is None
        assert err
