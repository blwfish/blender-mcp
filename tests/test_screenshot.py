"""
Tests for the screenshot MCP tool.

Verifies that image_base64 responses from Blender are returned as
mcp.types.ImageContent rather than a plain dict.
"""

import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.types import ImageContent

from blender_mcp.protocol import Command, ErrorCode


# 1×1 transparent PNG
PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
PNG_B64 = base64.b64encode(PNG_1x1).decode()


def make_mock_conn(result: dict | None = None, error=None):
    """Return a mock BlenderConnection whose send_command returns the given result/error."""
    resp = MagicMock()
    resp.is_success = error is None
    resp.result = result
    resp.error = error

    conn = MagicMock()
    conn.send_command = AsyncMock(return_value=resp)
    return conn


@pytest.fixture
def screenshot_fn():
    """Import the screenshot tool function after the server module is loaded."""
    from blender_mcp import server
    return server.screenshot


# ---------------------------------------------------------------------------
# Success: Blender returns image_base64
# ---------------------------------------------------------------------------

class TestScreenshotReturnsImageContent:
    @pytest.mark.asyncio
    async def test_returns_image_content_type(self, screenshot_fn):
        conn = make_mock_conn(result={"image_base64": PNG_B64, "width": 800, "height": 600})
        with patch("blender_mcp.server._ensure_connected", AsyncMock(return_value=conn)):
            result = await screenshot_fn(width=800, height=600)
        assert isinstance(result, ImageContent)

    @pytest.mark.asyncio
    async def test_mime_type_is_png(self, screenshot_fn):
        conn = make_mock_conn(result={"image_base64": PNG_B64, "width": 800, "height": 600})
        with patch("blender_mcp.server._ensure_connected", AsyncMock(return_value=conn)):
            result = await screenshot_fn()
        assert result.mimeType == "image/png"

    @pytest.mark.asyncio
    async def test_image_data_matches_blender_output(self, screenshot_fn):
        conn = make_mock_conn(result={"image_base64": PNG_B64, "width": 1, "height": 1})
        with patch("blender_mcp.server._ensure_connected", AsyncMock(return_value=conn)):
            result = await screenshot_fn()
        assert base64.b64decode(result.data) == PNG_1x1

    @pytest.mark.asyncio
    async def test_send_command_called_with_screenshot(self, screenshot_fn):
        conn = make_mock_conn(result={"image_base64": PNG_B64, "width": 800, "height": 600})
        with patch("blender_mcp.server._ensure_connected", AsyncMock(return_value=conn)):
            await screenshot_fn(width=800, height=600)
        conn.send_command.assert_called_once()
        cmd_arg = conn.send_command.call_args[0][0]
        assert cmd_arg == Command.SCREENSHOT

    @pytest.mark.asyncio
    async def test_filepath_forwarded_when_provided(self, screenshot_fn):
        conn = make_mock_conn(result={"filepath": "/tmp/shot.png", "width": 800, "height": 600})
        with patch("blender_mcp.server._ensure_connected", AsyncMock(return_value=conn)):
            result = await screenshot_fn(filepath="/tmp/shot.png")
        # When Blender saves to a file it doesn't return image_base64 — we get a dict
        assert isinstance(result, dict)
        assert result["status"] == "success"
        params = conn.send_command.call_args[0][1]
        assert params["filepath"] == "/tmp/shot.png"


# ---------------------------------------------------------------------------
# Error paths — tool returns a dict, not ImageContent
# ---------------------------------------------------------------------------

class TestScreenshotErrors:
    @pytest.mark.asyncio
    async def test_blender_error_returns_dict(self, screenshot_fn):
        err = MagicMock()
        err.code = ErrorCode.EXECUTION_ERROR
        err.message = "Blender exploded"
        err.traceback = None
        err.context = None
        conn = make_mock_conn(error=err)
        with patch("blender_mcp.server._ensure_connected", AsyncMock(return_value=conn)):
            result = await screenshot_fn()
        assert isinstance(result, dict)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_connection_error_returns_dict(self, screenshot_fn):
        from blender_mcp.connection import BlenderConnectionError
        with patch(
            "blender_mcp.server._ensure_connected",
            AsyncMock(side_effect=BlenderConnectionError(ErrorCode.CONNECTION_REFUSED, "refused")),
        ):
            result = await screenshot_fn()
        assert isinstance(result, dict)
        assert result["status"] == "error"
