"""
Integration tests — requires Blender running with the Blender MCP Bridge addon enabled.

Run with:
    pytest tests/test_integration.py --blender-running

These tests are skipped unless --blender-running is passed, ensuring CI doesn't
attempt to run them without Blender available.
"""

import socket
import pytest

from blender_mcp.connection import BlenderConnection, BlenderConnectionError
from blender_mcp.protocol import Command


def _blender_available() -> bool:
    """Check if Blender is running with the addon on the default port."""
    try:
        s = socket.create_connection(("127.0.0.1", 9876), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def pytest_addoption(parser):
    parser.addoption(
        "--blender-running",
        action="store_true",
        default=False,
        help="Run integration tests that require Blender with Blender MCP Bridge addon",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: mark test as requiring Blender (use --blender-running)"
    )


requires_blender = pytest.mark.skipif(
    not _blender_available(),
    reason="Blender not running on localhost:9876 — start Blender with Blender MCP Bridge addon",
)


@requires_blender
class TestFullStackSmoke:
    """Smoke tests: one round-trip per tool."""

    @pytest.fixture(autouse=True)
    async def conn(self):
        c = BlenderConnection()
        await c.connect()
        yield c
        await c.disconnect()

    @pytest.mark.asyncio
    async def test_ping(self, conn):
        result = await conn.ping()
        assert result["status"] == "ok"
        assert result["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_get_version(self, conn):
        resp = await conn.send_command(Command.GET_VERSION)
        assert resp.is_success
        assert "blender_version" in resp.result
        assert "addon_version" in resp.result

    @pytest.mark.asyncio
    async def test_get_scene_info_summary(self, conn):
        resp = await conn.send_command(Command.GET_SCENE_INFO, {"detail_level": "summary"})
        assert resp.is_success
        r = resp.result
        assert "scene_name" in r
        assert "object_count" in r
        assert isinstance(r["objects"], list)

    @pytest.mark.asyncio
    async def test_execute_code_simple(self, conn):
        resp = await conn.send_command(
            Command.EXECUTE_CODE,
            {"code": "import bpy; result = len(bpy.data.objects)"},
        )
        assert resp.is_success
        assert "execution_time" in resp.result

    @pytest.mark.asyncio
    async def test_execute_code_error(self, conn):
        resp = await conn.send_command(
            Command.EXECUTE_CODE,
            {"code": "raise ValueError('test error from integration test')"},
        )
        assert not resp.is_success
        assert resp.error.code == "EXECUTION_ERROR"
        assert "test error from integration test" in resp.error.message

    @pytest.mark.asyncio
    async def test_get_scene_info_mesh_detail(self, conn):
        resp = await conn.send_command(Command.GET_SCENE_INFO, {"detail_level": "mesh"})
        assert resp.is_success

    @pytest.mark.asyncio
    async def test_check_printability_on_default_cube(self, conn):
        """Blender's default cube is manifold — should pass printability check."""
        # Ensure a cube exists
        await conn.send_command(
            Command.EXECUTE_CODE,
            {"code": "import bpy; bpy.ops.mesh.primitive_cube_add()"},
        )
        resp_info = await conn.send_command(Command.GET_SCENE_INFO, {"detail_level": "summary"})
        objects = resp_info.result.get("objects", [])
        cube_name = next(
            (o["name"] for o in objects if o["type"] == "MESH"), None
        )
        if cube_name is None:
            pytest.skip("No mesh object in scene")

        resp = await conn.send_command(
            Command.CHECK_PRINTABILITY,
            {"object_name": cube_name, "target_scale": 0.01148},
        )
        assert resp.is_success
        r = resp.result
        assert "is_manifold" in r
        assert "printable" in r
        assert r["is_manifold"] is True  # default cube is manifold

    @pytest.mark.asyncio
    async def test_export_and_file_exists(self, conn, tmp_path):
        """Create a cube and export it; verify the file is written."""
        import os

        # Create a cube
        await conn.send_command(
            Command.EXECUTE_CODE,
            {"code": "import bpy; bpy.ops.object.select_all(action='SELECT')"},
        )
        output = str(tmp_path / "test_export.stl")
        resp = await conn.send_command(
            Command.EXPORT_MESH,
            {"filepath": output, "format": "stl", "scale": 1.0, "validate": True},
        )
        assert resp.is_success
        r = resp.result
        assert r["filepath"] == output
        assert os.path.exists(output)
        assert r["file_size_bytes"] > 0
