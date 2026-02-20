"""
COVA Blender MCP — TCP Connection Management

Manages the async TCP connection from the MCP server to the Blender addon.
Handles connection lifecycle, timeouts, and all failure modes with specific,
actionable error messages.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .protocol import (
    PROTOCOL_VERSION,
    Command,
    ErrorCode,
    Request,
    Response,
    make_error_response,
    parse_response,
    versions_compatible,
)

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
CONNECT_TIMEOUT = 5.0   # seconds for initial TCP connect
DEFAULT_TIMEOUT = 30.0  # seconds for command execution


class BlenderConnectionError(Exception):
    """Raised when the TCP connection to Blender fails or is lost."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BlenderConnection:
    """
    Async TCP connection to the Blender addon.

    Thread-safety: This class is designed for use in a single asyncio event loop.
    FastMCP tools are async; they share a single connection instance.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._connection_count = 0
        self._connected_at: float | None = None
        self._blender_version: str | None = None
        self._addon_version: str | None = None
        self._remote_protocol_version: str | None = None

    # ─── Connection Lifecycle ─────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish TCP connection and perform version handshake."""
        logger.info("Connecting to Blender on %s:%d", self.host, self.port)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=CONNECT_TIMEOUT,
            )
        except ConnectionRefusedError:
            raise BlenderConnectionError(
                ErrorCode.CONNECTION_REFUSED,
                f"Cannot connect to Blender on {self.host}:{self.port}. "
                "Ensure Blender is running with the COVA MCP Bridge addon enabled "
                "and the server is started (View3D > Sidebar > COVA MCP).",
            )
        except asyncio.TimeoutError:
            raise BlenderConnectionError(
                ErrorCode.CONNECTION_TIMEOUT,
                f"Connection to Blender timed out after {CONNECT_TIMEOUT:.0f}s. "
                f"Check that Blender is running and the addon is listening on port {self.port}.",
            )
        except OSError as e:
            raise BlenderConnectionError(
                ErrorCode.CONNECTION_REFUSED,
                f"Failed to connect to Blender: {e}",
            )

        self._connected = True
        self._connection_count += 1
        self._connected_at = time.monotonic()
        logger.info("TCP connection established")

        # Handshake
        await self._handshake()

    async def _handshake(self) -> None:
        """Send get_version and validate protocol compatibility."""
        logger.debug("Performing protocol handshake")
        req = Request(command=Command.GET_VERSION)
        resp = await self._send_raw(req, timeout=5.0)

        if not resp.is_success:
            err = resp.error
            raise BlenderConnectionError(
                err.code if err else ErrorCode.INTERNAL_ERROR,
                f"Handshake failed: {err.message if err else 'Unknown error'}",
            )

        result = resp.result or {}
        remote_ver = result.get("protocol_version", "unknown")
        self._remote_protocol_version = remote_ver
        self._blender_version = result.get("blender_version", "unknown")
        self._addon_version = result.get("addon_version", "unknown")

        if not versions_compatible(PROTOCOL_VERSION, remote_ver):
            self._connected = False
            raise BlenderConnectionError(
                ErrorCode.VERSION_MISMATCH,
                f"Protocol version mismatch: MCP server uses {PROTOCOL_VERSION}, "
                f"Blender addon uses {remote_ver}. "
                f"Update the {'addon' if remote_ver < PROTOCOL_VERSION else 'MCP server'} "
                "to resolve this.",
            )

        logger.info(
            "Handshake OK — Blender %s, addon %s, protocol %s",
            self._blender_version,
            self._addon_version,
            remote_ver,
        )

    async def disconnect(self) -> None:
        """Close the TCP connection cleanly."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self._connected = False
        logger.info("Disconnected from Blender")

    async def reconnect(self) -> dict[str, Any]:
        """Disconnect and reconnect. Returns new status."""
        logger.info("Reconnecting to Blender")
        await self.disconnect()
        await self.connect()
        return self.status()

    def status(self) -> dict[str, Any]:
        """Return current connection state."""
        uptime = None
        if self._connected_at is not None:
            uptime = round(time.monotonic() - self._connected_at, 1)
        return {
            "connected": self._connected,
            "host": self.host,
            "port": self.port,
            "blender_version": self._blender_version,
            "addon_version": self._addon_version,
            "protocol_version": self._remote_protocol_version,
            "mcp_protocol_version": PROTOCOL_VERSION,
            "uptime_seconds": uptime,
            "connection_count": self._connection_count,
        }

    # ─── Command Dispatch ─────────────────────────────────────────────────

    async def send_command(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> Response:
        """
        Send a command to Blender and return the Response.

        Raises BlenderConnectionError on connection failure.
        Returns a Response with status="error" on Blender-side errors.
        """
        if not self._connected:
            raise BlenderConnectionError(
                ErrorCode.CONNECTION_LOST,
                "Not connected to Blender. Use manage_connection(action='reconnect') "
                "to re-establish the connection.",
            )

        req = Request(command=command, params=params or {})
        logger.debug("→ %s %s", command, params)

        try:
            resp = await self._send_raw(req, timeout=timeout)
        except BlenderConnectionError:
            self._connected = False
            raise

        logger.debug("← %s %s", resp.status, resp.result or resp.error)
        return resp

    async def _send_raw(self, req: Request, timeout: float) -> Response:
        """Low-level send/receive with timeout and connection-loss detection."""
        assert self._writer is not None
        assert self._reader is not None

        payload = req.to_json().encode()
        try:
            self._writer.write(payload)
            await self._writer.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            self._connected = False
            raise BlenderConnectionError(
                ErrorCode.CONNECTION_LOST,
                f"Lost connection to Blender while sending command: {e}. "
                "Use manage_connection(action='reconnect') to re-establish.",
            )

        try:
            raw = await asyncio.wait_for(
                self._reader.readline(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Don't mark disconnected — Blender may still complete the command
            return make_error_response(
                req.message_id,
                ErrorCode.TIMEOUT,
                f"Command '{req.command}' timed out after {timeout:.0f}s. "
                "The command may still be running in Blender.",
            )
        except (ConnectionResetError, OSError) as e:
            self._connected = False
            raise BlenderConnectionError(
                ErrorCode.CONNECTION_LOST,
                f"Lost connection to Blender while waiting for response: {e}. "
                "Use manage_connection(action='reconnect') to re-establish.",
            )

        if not raw:
            self._connected = False
            raise BlenderConnectionError(
                ErrorCode.CONNECTION_LOST,
                "Blender closed the connection (possibly crashed). "
                "Use manage_connection(action='reconnect') after restarting Blender.",
            )

        resp, parse_err = parse_response(raw.decode(errors="replace"))
        if parse_err or resp is None:
            return make_error_response(
                req.message_id,
                ErrorCode.INTERNAL_ERROR,
                f"Received malformed response from Blender: {parse_err}. "
                f"Raw (truncated): {raw[:200]!r}",
            )

        return resp

    # ─── Ping ─────────────────────────────────────────────────────────────

    async def ping(self) -> dict[str, Any]:
        """Measure round-trip latency to Blender."""
        t0 = time.monotonic()
        resp = await self.send_command(Command.PING, timeout=5.0)
        elapsed_ms = round((time.monotonic() - t0) * 1000, 2)

        if resp.is_success:
            return {"latency_ms": elapsed_ms, "status": "ok"}
        return {
            "latency_ms": elapsed_ms,
            "status": "error",
            "error": resp.error.message if resp.error else "Unknown",
        }
