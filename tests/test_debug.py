"""
Debug infrastructure tests — no Blender required, no network required.
Tests the debugger and health monitor in isolation.
"""

import asyncio
import os
import time
import pytest

from blender_mcp.debug import BlenderMCPDebugger, init_debugger, get_debugger


# ─── BlenderMCPDebugger ───────────────────────────────────────────────────────

class TestDebugger:
    @pytest.fixture
    def dbg(self, tmp_path):
        return BlenderMCPDebugger(log_dir=str(tmp_path), lean=False)

    @pytest.fixture
    def lean_dbg(self, tmp_path):
        return BlenderMCPDebugger(log_dir=str(tmp_path / "lean"), lean=True)

    def test_log_dir_created(self, tmp_path):
        subdir = tmp_path / "newdir"
        BlenderMCPDebugger(log_dir=str(subdir))
        assert subdir.exists()

    def test_log_file_created(self, dbg, tmp_path):
        assert (tmp_path / "blender_mcp.log").exists()

    def test_log_operation_success(self, dbg, tmp_path):
        dbg.log_operation("test_op", parameters={"x": 1}, duration=0.1)
        log = (tmp_path / "blender_mcp.log").read_text()
        assert "test_op" in log
        assert "✓" in log

    def test_log_operation_error(self, dbg, tmp_path):
        try:
            raise ValueError("boom")
        except ValueError as e:
            dbg.log_operation("test_op", error=e, duration=0.05)
        log = (tmp_path / "blender_mcp.log").read_text()
        assert "✗" in log
        assert "boom" in log

    def test_op_json_log_written_verbose(self, dbg, tmp_path):
        dbg.log_operation("an_op", parameters={"a": "b"}, duration=0.1)
        json_files = list(tmp_path.glob("operations_*.json"))
        assert len(json_files) == 1
        content = json_files[0].read_text()
        assert "an_op" in content

    def test_op_json_log_errors_always_written_in_lean(self, lean_dbg, tmp_path):
        subdir = tmp_path / "lean"
        try:
            raise RuntimeError("err")
        except RuntimeError as e:
            lean_dbg.log_operation("fail_op", error=e, duration=0.01)
        json_files = list(subdir.glob("operations_*.json"))
        assert len(json_files) == 1
        assert "fail_op" in json_files[0].read_text()

    def test_track_performance(self, dbg):
        for i in range(5):
            dbg.track_performance("my_tool", 0.1 * (i + 1))
        report = dbg.performance_report()
        assert "my_tool" in report
        stats = report["my_tool"]
        assert stats["count"] == 5
        assert stats["min_ms"] == pytest.approx(100.0, rel=0.01)
        assert stats["max_ms"] == pytest.approx(500.0, rel=0.01)

    def test_performance_report_summary(self, dbg):
        report = dbg.performance_report()
        assert "_summary" in report
        assert "total_calls" in report["_summary"]
        assert "mode" in report["_summary"]
        assert report["_summary"]["mode"] == "VERBOSE"

    def test_lean_mode_label(self, lean_dbg):
        report = lean_dbg.performance_report()
        assert report["_summary"]["mode"] == "LEAN"

    def test_performance_keeps_last_100(self, dbg):
        for i in range(150):
            dbg.track_performance("heavy", 0.001)
        report = dbg.performance_report()
        assert report["heavy"]["count"] == 100  # deque maxlen

    def test_export_debug_package(self, dbg):
        pkg = dbg.export_debug_package()
        assert "log_dir" in pkg
        assert "performance" in pkg
        assert "_summary" in pkg["performance"]

    @pytest.mark.asyncio
    async def test_tool_decorator_success(self, dbg):
        @dbg.tool_decorator()
        async def my_tool(x: int) -> dict:
            return {"result": x * 2}

        result = await my_tool(x=5)
        assert result == {"result": 10}

        report = dbg.performance_report()
        assert "my_tool" in report
        assert report["my_tool"]["count"] == 1

    @pytest.mark.asyncio
    async def test_tool_decorator_records_error(self, dbg):
        @dbg.tool_decorator()
        async def broken_tool() -> dict:
            raise RuntimeError("deliberate error")

        with pytest.raises(RuntimeError):
            await broken_tool()

        # Error count incremented
        assert dbg._error_count == 1

    @pytest.mark.asyncio
    async def test_tool_decorator_preserves_signature(self, dbg):
        async def my_tool(a: str, b: int = 0) -> dict:
            """My docstring."""
            return {}

        wrapped = dbg.tool_decorator()(my_tool)
        assert wrapped.__name__ == "my_tool"
        assert wrapped.__doc__ == "My docstring."

    @pytest.mark.asyncio
    async def test_tool_decorator_lean_truncates_code(self, lean_dbg):
        """In lean mode, 'code' parameter is summarized, not logged in full."""
        logged_params = []
        orig = lean_dbg.log_operation

        def capturing_log(operation, parameters=None, **kw):
            logged_params.append(parameters)
            return orig(operation, parameters=parameters, **kw)

        lean_dbg.log_operation = capturing_log

        @lean_dbg.tool_decorator()
        async def execute_blender_code(code: str) -> dict:
            return {}

        await execute_blender_code(code="x = 1")
        # In lean mode with no error, parameters are not logged (None)
        assert logged_params[-1] is None


# ─── Singleton ────────────────────────────────────────────────────────────────

class TestSingleton:
    def test_init_debugger_returns_instance(self, tmp_path):
        dbg = init_debugger(log_dir=str(tmp_path))
        assert isinstance(dbg, BlenderMCPDebugger)

    def test_get_debugger_returns_same(self, tmp_path):
        dbg = init_debugger(log_dir=str(tmp_path))
        assert get_debugger() is dbg
