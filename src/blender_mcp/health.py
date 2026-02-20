"""
Blender MCP — Connection Health Monitor

Tracks the health of the TCP connection to Blender, runs periodic background
pings, records failure history, and provides exportable status reports.

Adapted from freecad-mcp/AICopilot/freecad_health.py, with modifications
for the async TCP model and FastMCP tooling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .connection import BlenderConnection

logger = logging.getLogger("blender_mcp.health")

_PING_INTERVAL = 30.0   # seconds between background pings
_PING_TIMEOUT  = 5.0    # seconds to wait for ping response
_MAX_HISTORY   = 50     # maximum events kept in history


class ConnectionHealthMonitor:
    """
    Monitors the TCP connection to Blender.

    Background task sends periodic pings and records the outcome. All
    connection state changes (success, failure, reconnect) are logged and
    stored in a bounded history for diagnostic export.
    """

    def __init__(self, connection: "BlenderConnection"):
        self._conn = connection
        self._task: asyncio.Task | None = None

        # Counters
        self.total_pings = 0
        self.successful_pings = 0
        self.consecutive_failures = 0
        self.reconnect_attempts = 0

        # Timing
        self.started_at: float = time.monotonic()
        self.last_success_at: float | None = None
        self.last_failure_at: float | None = None

        # Event history (bounded)
        self._history: list[dict[str, Any]] = []

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background health-check task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._ping_loop(), name="blender-mcp-health"
            )
            logger.info("Health monitor started (interval: %.0fs)", _PING_INTERVAL)

    def stop(self) -> None:
        """Cancel the background task."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Health monitor stopped")

    # ─── Background Loop ──────────────────────────────────────────────────────

    async def _ping_loop(self) -> None:
        """Periodic health check loop. Runs until cancelled."""
        await asyncio.sleep(_PING_INTERVAL)  # don't ping immediately on startup
        while True:
            try:
                await self._check()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("Health check loop error (non-fatal): %s", e)
            await asyncio.sleep(_PING_INTERVAL)

    async def _check(self) -> None:
        """Perform one ping and record the outcome."""
        self.total_pings += 1
        t0 = time.monotonic()

        if not self._conn._connected:
            self._record_failure("not_connected", latency_ms=None)
            return

        try:
            result = await asyncio.wait_for(self._conn.ping(), timeout=_PING_TIMEOUT)
            latency_ms = result.get("latency_ms")
            if result.get("status") == "ok":
                self._record_success(latency_ms)
            else:
                self._record_failure(result.get("error", "ping_error"), latency_ms)
        except asyncio.TimeoutError:
            self._record_failure("ping_timeout", latency_ms=None)
        except Exception as e:
            self._record_failure(str(e), latency_ms=None)

    # ─── State Recording ──────────────────────────────────────────────────────

    def _record_success(self, latency_ms: float | None) -> None:
        prev_failures = self.consecutive_failures
        self.consecutive_failures = 0
        self.successful_pings += 1
        self.last_success_at = time.monotonic()

        if prev_failures > 0:
            # Recovery after failures
            event = "recovered"
            logger.info("✓ Blender connection recovered after %d failure(s)", prev_failures)
        else:
            event = "ok"
            logger.debug("✓ ping %.1fms", latency_ms or 0)

        self._append_history({
            "event": event,
            "latency_ms": latency_ms,
            "consecutive_failures_before": prev_failures,
        })

    def _record_failure(self, reason: str, latency_ms: float | None) -> None:
        self.consecutive_failures += 1
        self.last_failure_at = time.monotonic()

        logger.warning(
            "✗ Blender ping failed (%s) — consecutive: %d",
            reason, self.consecutive_failures
        )
        self._append_history({
            "event": "failure",
            "reason": reason,
            "latency_ms": latency_ms,
            "consecutive_failures": self.consecutive_failures,
        })

    def record_reconnect_attempt(self, success: bool) -> None:
        """Call this from manage_connection(action='reconnect')."""
        self.reconnect_attempts += 1
        self._append_history({
            "event": "reconnect_attempt",
            "success": success,
            "attempt_number": self.reconnect_attempts,
        })
        if success:
            self.consecutive_failures = 0
            self.last_success_at = time.monotonic()
            logger.info("Reconnect #%d succeeded", self.reconnect_attempts)
        else:
            logger.warning("Reconnect #%d failed", self.reconnect_attempts)

    def record_connection_lost(self, reason: str) -> None:
        """Call this when a command fails due to connection loss."""
        self._append_history({"event": "connection_lost", "reason": reason})
        logger.error("Connection lost: %s", reason)

    def _append_history(self, entry: dict[str, Any]) -> None:
        entry["ts"] = datetime.now().isoformat()
        self._history.append(entry)
        if len(self._history) > _MAX_HISTORY:
            self._history.pop(0)

    # ─── Status / Export ──────────────────────────────────────────────────────

    @property
    def is_healthy(self) -> bool:
        return self._conn._connected and self.consecutive_failures == 0

    @property
    def uptime_s(self) -> float:
        return round(time.monotonic() - self.started_at, 1)

    def get_status(self) -> dict[str, Any]:
        """Return current health state — included in manage_connection status."""
        last_ok = (
            round(time.monotonic() - self.last_success_at, 1)
            if self.last_success_at is not None
            else None
        )
        return {
            "healthy": self.is_healthy,
            "total_pings": self.total_pings,
            "successful_pings": self.successful_pings,
            "consecutive_failures": self.consecutive_failures,
            "reconnect_attempts": self.reconnect_attempts,
            "last_success_ago_s": last_ok,
            "monitor_uptime_s": self.uptime_s,
            "ping_interval_s": _PING_INTERVAL,
        }

    def export_report(self) -> dict[str, Any]:
        """Full diagnostic report including event history."""
        return {
            **self.get_status(),
            "history": list(self._history),
        }


# ─── Singleton ────────────────────────────────────────────────────────────────

_monitor: ConnectionHealthMonitor | None = None


def init_monitor(connection: "BlenderConnection") -> ConnectionHealthMonitor:
    """Initialize the global health monitor. Call after first connect attempt."""
    global _monitor
    _monitor = ConnectionHealthMonitor(connection)
    return _monitor


def get_monitor() -> ConnectionHealthMonitor | None:
    """Return the global health monitor, or None if not initialized."""
    return _monitor
