"""
pytest configuration for Blender MCP tests.

Unit tests (test_protocol.py, test_server.py) require no external processes.
Addon tests (test_addon.py) require Blender running headless.
Integration tests (test_integration.py) require Blender with addon enabled.
"""

import asyncio
import json
import socket
import threading
from typing import Any, Generator

import pytest

from blender_mcp.protocol import PROTOCOL_VERSION, Request, Response


# ─── Mock Blender TCP Server ─────────────────────────────────────────────────

class MockBlenderServer:
    """
    A simple TCP server that mimics the Blender addon for testing the MCP server
    without requiring Blender to be installed or running.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.responses: dict[str, Any] = {}  # command → response dict (success)
        self.errors: dict[str, tuple[str, str]] = {}  # command → (code, message)
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.received_requests: list[dict] = []

    def set_response(self, command: str, result: dict) -> None:
        """Configure a success response for a command."""
        self.responses[command] = result

    def set_error(self, command: str, code: str, message: str) -> None:
        """Configure an error response for a command."""
        self.errors[command] = (code, message)

    @property
    def port(self) -> int:
        if self._sock:
            return self._sock.getsockname()[1]
        return 0

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, 0))
        self._sock.listen(5)
        self._sock.settimeout(0.5)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sock:
            self._sock.close()

    def _serve(self) -> None:
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except (OSError, socket.timeout):
                continue
            self._handle(conn)

    def _handle(self, conn: socket.socket) -> None:
        buf = b""
        try:
            while self._running:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    req = json.loads(line.decode())
                    self.received_requests.append(req)
                    resp = self._make_response(req)
                    conn.sendall(resp.encode())
        finally:
            conn.close()

    def _make_response(self, req: dict) -> str:
        message_id = req.get("message_id", "")
        command = req.get("command", "")

        if command == "get_version":
            result = {
                "protocol_version": PROTOCOL_VERSION,
                "addon_version": "0.1.0",
                "blender_version": "4.0.0",
                "available_commands": ["execute_code", "get_scene_info", "ping", "get_version"],
            }
            return json.dumps({
                "protocol_version": PROTOCOL_VERSION,
                "message_id": message_id,
                "status": "success",
                "result": result,
            }) + "\n"

        if command in self.errors:
            code, msg = self.errors[command]
            return json.dumps({
                "protocol_version": PROTOCOL_VERSION,
                "message_id": message_id,
                "status": "error",
                "error": {"code": code, "message": msg},
            }) + "\n"

        result = self.responses.get(command, {"echo": command})
        return json.dumps({
            "protocol_version": PROTOCOL_VERSION,
            "message_id": message_id,
            "status": "success",
            "result": result,
        }) + "\n"


@pytest.fixture
def mock_server() -> Generator[MockBlenderServer, None, None]:
    server = MockBlenderServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def event_loop():
    """Provide an asyncio event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
