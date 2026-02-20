"""
Blender MCP — Debug Infrastructure

File-based logging and performance tracking for the MCP server process.

CRITICAL: MCP servers communicate over stdio. Any writes to stdout corrupt
the transport. This module uses file-only logging exclusively.

Adapted from freecad-mcp/AICopilot/freecad_debug.py, with modifications
for FastMCP async tools and the Blender TCP connection model.

Configuration via environment variables:
  BLENDERMCP_LOG_DIR    Log directory (default: /tmp/blender_mcp_debug)
  BLENDERMCP_LOG_LEVEL  DEBUG enables verbose mode; anything else is lean
                        (default: INFO → lean)
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Callable

# ─── Configuration ────────────────────────────────────────────────────────────

_DEFAULT_LOG_DIR = "/tmp/blender_mcp_debug"
_LOG_DIR = os.environ.get("BLENDERMCP_LOG_DIR", _DEFAULT_LOG_DIR)
_LEAN = os.environ.get("BLENDERMCP_LOG_LEVEL", "INFO").upper() != "DEBUG"


# ─── Debugger ─────────────────────────────────────────────────────────────────

class BlenderMCPDebugger:
    """
    Debugging and performance tracking for the Blender MCP server.

    Modes:
      LEAN    (default): Only errors and operation summaries are logged.
                         ~60% less log volume. Good for normal use.
      VERBOSE (DEBUG):   Full parameters, results, and timing for every op.
                         Set BLENDERMCP_LOG_LEVEL=DEBUG to enable.
    """

    def __init__(self, log_dir: str = _LOG_DIR, lean: bool = _LEAN):
        self.log_dir = log_dir
        self.lean = lean
        self._perf: dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._op_count = 0
        self._error_count = 0
        self._started_at = datetime.now()
        self.logger = self._setup_logging()

    def _setup_logging(self) -> logging.Logger:
        os.makedirs(self.log_dir, exist_ok=True)

        log = logging.getLogger("blender_mcp")
        log.setLevel(logging.DEBUG)  # handlers filter individually
        log.propagate = False  # don't leak to root logger → stdout

        # Always reset handlers so re-initialization points to the current log_dir.
        # In production there is only ever one instance (singleton); in tests each
        # fixture gets its own tmp_path with its own handler.
        for h in log.handlers[:]:
            log.removeHandler(h)
            h.close()

        log_file = os.path.join(self.log_dir, "blender_mcp.log")
        fh = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG if not self.lean else logging.INFO)
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s")
        )
        log.addHandler(fh)

        # Daily JSON operation log (machine-readable for post-hoc analysis)
        self._op_log_path = os.path.join(
            self.log_dir, f"operations_{datetime.now():%Y%m%d}.json"
        )

        mode = "LEAN" if self.lean else "VERBOSE"
        log.info("BlenderMCPDebugger initialized (%s) — logs: %s", mode, self.log_dir)
        return log

    # ─── Operation Logging ────────────────────────────────────────────────────

    def log_operation(
        self,
        operation: str,
        parameters: dict | None = None,
        result: Any = None,
        error: Exception | None = None,
        duration: float | None = None,
    ) -> None:
        """
        Log a single tool invocation with timing and outcome.

        In LEAN mode: only errors are written to the JSON op-log;
        summaries always go to the rotating text log.
        In VERBOSE mode: all ops written to both logs.
        """
        self._op_count += 1
        if error:
            self._error_count += 1

        duration_ms = round(duration * 1000, 2) if duration is not None else None
        status = "error" if error else "success"
        icon = "✗" if error else "✓"

        # Text log — always write, level depends on outcome
        level = logging.ERROR if error else (logging.DEBUG if self.lean else logging.INFO)
        self.logger.log(
            level,
            "%s %-30s %s%s",
            icon,
            operation,
            f"{duration_ms:.0f}ms" if duration_ms is not None else "?ms",
            f"  ERROR: {error}" if error else "",
        )

        # Verbose: also log parameters / result summary
        if not self.lean or error:
            if parameters:
                safe_params = {
                    k: (v[:200] if isinstance(v, str) and len(v) > 200 else v)
                    for k, v in parameters.items()
                }
                self.logger.debug("  params: %s", safe_params)
            if error:
                self.logger.error("  traceback:\n%s", traceback.format_exc())

        # JSON op-log — always write errors; in verbose mode write everything
        if error or not self.lean:
            entry: dict[str, Any] = {
                "ts": datetime.now().isoformat(),
                "op": operation,
                "status": status,
                "duration_ms": duration_ms,
            }
            if parameters and (not self.lean or error):
                entry["params"] = {
                    k: (v[:500] if isinstance(v, str) and len(v) > 500 else v)
                    for k, v in parameters.items()
                }
            if error:
                entry["error"] = str(error)
                entry["traceback"] = traceback.format_exc()
            elif result is not None and not self.lean:
                entry["result_summary"] = str(result)[:300]

            try:
                with open(self._op_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError as e:
                self.logger.warning("Could not write op-log: %s", e)

    # ─── Performance Tracking ─────────────────────────────────────────────────

    def track_performance(self, operation: str, duration: float) -> None:
        """Record a timing sample for an operation (keeps last 100 per op)."""
        self._perf[operation].append(duration)

    def performance_report(self) -> dict[str, Any]:
        """Return per-operation timing statistics."""
        report: dict[str, Any] = {
            "_summary": {
                "total_calls": self._op_count,
                "total_errors": self._error_count,
                "uptime_s": round((datetime.now() - self._started_at).total_seconds(), 1),
                "log_dir": self.log_dir,
                "mode": "LEAN" if self.lean else "VERBOSE",
            }
        }
        for op, times in self._perf.items():
            t = list(times)
            if t:
                report[op] = {
                    "count": len(t),
                    "avg_ms": round(sum(t) / len(t) * 1000, 2),
                    "min_ms": round(min(t) * 1000, 2),
                    "max_ms": round(max(t) * 1000, 2),
                    "last_ms": round(t[-1] * 1000, 2),
                }
        return report

    # ─── Decorator ────────────────────────────────────────────────────────────

    def tool_decorator(self) -> Callable[[Callable], Callable]:
        """
        Async decorator for MCP tool functions.

        Wraps the function to capture timing, parameters, and exceptions,
        then calls log_operation() and track_performance().
        The decorated function behaves identically from the caller's perspective.

        Usage:
            dbg = get_debugger()

            @mcp.tool()
            @dbg.tool_decorator()
            async def my_tool(param: str) -> dict:
                ...
        """
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                t0 = time.monotonic()
                error: Exception | None = None
                result = None
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    error = e
                    raise
                finally:
                    duration = time.monotonic() - t0
                    # Don't log code bodies in LEAN mode (can be large)
                    params = dict(kwargs) if kwargs else {}
                    if self.lean and "code" in params:
                        params = {**params, "code": f"<{len(params['code'])} chars>"}
                    self.log_operation(
                        operation=func.__name__,
                        parameters=params if not self.lean or error else None,
                        result=result,
                        error=error,
                        duration=duration,
                    )
                    self.track_performance(func.__name__, duration)

            return wrapper
        return decorator

    # ─── Diagnostics ──────────────────────────────────────────────────────────

    def export_debug_package(self) -> dict[str, Any]:
        """
        Return a summary dict suitable for including in a manage_connection
        status response or crash report.
        """
        return {
            "log_dir": self.log_dir,
            "op_log": self._op_log_path,
            "performance": self.performance_report(),
        }


# ─── Singleton ────────────────────────────────────────────────────────────────

_debugger: BlenderMCPDebugger | None = None


def init_debugger(
    log_dir: str = _LOG_DIR,
    lean: bool = _LEAN,
) -> BlenderMCPDebugger:
    """Initialize the global debugger. Call once at server startup."""
    global _debugger
    _debugger = BlenderMCPDebugger(log_dir=log_dir, lean=lean)
    return _debugger


def get_debugger() -> BlenderMCPDebugger | None:
    """Return the global debugger, or None if not yet initialized."""
    return _debugger
