"""
Blender MCP — FastMCP Server

Exposes 7 MCP tools to Claude Code and translates them to TCP commands
directed at the Blender addon.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent

from .connection import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    BlenderConnection,
    BlenderConnectionError,
)
from .debug import init_debugger, get_debugger
from .health import init_monitor, get_monitor
from .protocol import Command, ErrorCode

# ─── Logging ──────────────────────────────────────────────────────────────────
# MCP servers must NOT write anything to stdout (corrupts the stdio transport).
# The debug module sets up file-based logging; we point the root logger at
# stderr only as a last-resort fallback for startup errors before debug init.

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(__import__("sys").stderr)],
)
logger = logging.getLogger(__name__)

# ─── Debug Infrastructure ─────────────────────────────────────────────────────
# Initialized here so file logging is available before any tool is called.
# Graceful degradation: if init fails we run without debug (log to stderr).

try:
    _dbg = init_debugger()
    logger.info("Debug infrastructure initialized — logs: %s", _dbg.log_dir)
except Exception as _e:
    _dbg = None
    logger.warning("Debug init failed (%s) — running without file logging", _e)

# ─── MCP Server ───────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="blender-mcp",
    instructions=(
        "Control Blender for organic geometry generation (trees, figures, terrain, "
        "rock faces) for HO-scale model railroad production. "
        "Blender must be running with the Blender MCP Bridge addon enabled before using these tools."
    ),
)

# Singleton connection shared across all tool invocations
_connection: BlenderConnection | None = None


def _get_connection() -> BlenderConnection:
    global _connection
    if _connection is None:
        host = os.environ.get("BLENDER_HOST", DEFAULT_HOST)
        port = int(os.environ.get("BLENDER_PORT", DEFAULT_PORT))
        _connection = BlenderConnection(host=host, port=port)
    return _connection


async def _ensure_connected() -> BlenderConnection:
    """Return a connected BlenderConnection, attempting to connect if needed."""
    conn = _get_connection()
    if not conn._connected:
        await conn.connect()
        # Spin up the health monitor after first successful connect
        monitor = get_monitor()
        if monitor is None:
            m = init_monitor(conn)
            m.start()
    return conn


def _connection_error_result(e: BlenderConnectionError) -> dict[str, Any]:
    monitor = get_monitor()
    if monitor:
        monitor.record_connection_lost(e.message)
    return {"status": "error", "error_code": e.code, "message": e.message}


def _blender_error_result(resp_error: Any) -> dict[str, Any]:
    if resp_error is None:
        return {"status": "error", "error_code": ErrorCode.INTERNAL_ERROR, "message": "Unknown error"}
    return {
        "status": "error",
        "error_code": resp_error.code,
        "message": resp_error.message,
        "traceback": resp_error.traceback,
        **({} if not resp_error.context else {"context": resp_error.context}),
    }


# ─── Decorator shorthand ──────────────────────────────────────────────────────
# Returns a no-op passthrough if debug is not initialized.

def _tool_dec():
    dbg = get_debugger()
    if dbg:
        return dbg.tool_decorator()
    import functools
    def _noop(f):
        @functools.wraps(f)
        async def wrapper(*a, **kw):
            return await f(*a, **kw)
        return wrapper
    return _noop


# ─── Tool 1: execute_blender_code ────────────────────────────────────────────

@mcp.tool()
@_tool_dec()
async def execute_blender_code(code: str, timeout: int = 30) -> dict[str, Any]:
    """
    Execute Python code in Blender's context.

    The code has access to `bpy` and all installed addon APIs. Use this for
    operations not covered by dedicated tools: mesh modeling, modifier application,
    addon-specific workflows (TheGrove, MPFB2, etc.).

    Args:
        code: Python code to execute in Blender's __main__ namespace.
        timeout: Seconds to wait for completion. Default 30. TheGrove growth
                 simulations may need 120+.

    Returns:
        status, result (stdout + return value), error (if failed), execution_time.
    """
    try:
        conn = await _ensure_connected()
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    try:
        resp = await conn.send_command(
            Command.EXECUTE_CODE,
            {"code": code, "timeout": timeout},
            timeout=float(timeout) + 5.0,
        )
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    if resp.is_success:
        return {"status": "success", **(resp.result or {})}
    return _blender_error_result(resp.error)


# ─── Tool 2: get_scene_info ───────────────────────────────────────────────────

@mcp.tool()
@_tool_dec()
async def get_scene_info(detail_level: str = "summary") -> dict[str, Any]:
    """
    Return structured information about the current Blender scene.

    Args:
        detail_level: One of "summary", "mesh", "full".
                      - summary: object names, types, count
                      - mesh: adds vertex/face counts, bounding box per mesh
                      - full: adds materials, modifier stack, armature info

    Returns:
        scene_name, object_count, unit_system, unit_scale, objects array.
    """
    if detail_level not in ("summary", "mesh", "full"):
        return {
            "status": "error",
            "error_code": ErrorCode.INVALID_PARAMS,
            "message": f"Invalid detail_level {detail_level!r}. Must be 'summary', 'mesh', or 'full'.",
        }

    try:
        conn = await _ensure_connected()
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    try:
        resp = await conn.send_command(
            Command.GET_SCENE_INFO,
            {"detail_level": detail_level},
        )
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    if resp.is_success:
        return {"status": "success", **(resp.result or {})}
    return _blender_error_result(resp.error)


# ─── Tool 3: export_mesh ─────────────────────────────────────────────────────

@mcp.tool()
@_tool_dec()
async def export_mesh(
    filepath: str,
    objects: list[str] | None = None,
    format: str = "stl",
    scale: float = 1.0,
    validate: bool = True,
) -> dict[str, Any]:
    """
    Export mesh objects to a file for production (3D printing / CNC).

    Modeling always occurs at full prototype scale (meters). Apply scale at
    export to produce correctly sized output. For HO scale from prototype:
    scale = 1/87.1 ≈ 0.01148.

    Args:
        filepath: Output file path (absolute path recommended).
        objects: Object names to export. Default: all selected mesh objects.
        format: One of "stl", "obj", "3mf". Default "stl".
        scale: Scale factor applied at export only. Default 1.0.
        validate: Run manifold check before export. Default True.

    Returns:
        filepath, format, object_count, total_vertices, total_faces,
        file_size_bytes, scale_applied, bounding_box_scaled, validation_result.
    """
    if format not in ("stl", "obj", "3mf"):
        return {
            "status": "error",
            "error_code": ErrorCode.INVALID_PARAMS,
            "message": f"Invalid format {format!r}. Must be 'stl', 'obj', or '3mf'.",
        }

    try:
        conn = await _ensure_connected()
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    params: dict[str, Any] = {
        "filepath": filepath,
        "format": format,
        "scale": scale,
        "validate": validate,
    }
    if objects is not None:
        params["objects"] = objects

    try:
        resp = await conn.send_command(Command.EXPORT_MESH, params, timeout=60.0)
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    if resp.is_success:
        return {"status": "success", **(resp.result or {})}
    return _blender_error_result(resp.error)


# ─── Tool 4: check_mesh_printability ─────────────────────────────────────────

@mcp.tool()
@_tool_dec()
async def check_mesh_printability(
    object_name: str,
    min_thickness: float = 0.005,
    target_scale: float = 0.01148,
) -> dict[str, Any]:
    """
    Analyze a mesh object for 3D printing readiness.

    Reports manifold status, thin features, loose geometry, and dimensions at
    both prototype scale and target (HO) scale. For resin printing on the
    AnyCubic M7 Pro/Max: features below 0.3mm at target scale are warnings;
    below 0.05mm are errors.

    Args:
        object_name: Name of the mesh object to analyze.
        min_thickness: Minimum wall thickness in scene units (meters at prototype
                       scale). Default 0.005 (5mm prototype ≈ 0.057mm at HO).
        target_scale: Scale factor for reporting. Default 0.01148 (HO 1:87.1).

    Returns:
        is_manifold, non_manifold_edges, non_manifold_verts, loose_geometry,
        degenerate_faces, self_intersections, thin_features, bounding_box,
        volume, printable (boolean summary).
    """
    try:
        conn = await _ensure_connected()
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    try:
        resp = await conn.send_command(
            Command.CHECK_PRINTABILITY,
            {
                "object_name": object_name,
                "min_thickness": min_thickness,
                "target_scale": target_scale,
            },
        )
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    if resp.is_success:
        return {"status": "success", **(resp.result or {})}
    return _blender_error_result(resp.error)


# ─── Tool 5: screenshot ───────────────────────────────────────────────────────

@mcp.tool()
@_tool_dec()
async def screenshot(
    filepath: str | None = None,
    width: int = 1920,
    height: int = 1080,
) -> dict[str, Any]:
    """
    Capture the current 3D viewport as a PNG image for visual verification.

    Args:
        filepath: Path to save the screenshot. If omitted, returns base64-encoded PNG.
        width: Image width in pixels. Default 1920.
        height: Image height in pixels. Default 1080.

    Returns:
        filepath (if saved) or image_base64 (if not), width, height.
    """
    try:
        conn = await _ensure_connected()
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    params: dict[str, Any] = {"width": width, "height": height}
    if filepath is not None:
        params["filepath"] = filepath

    try:
        resp = await conn.send_command(Command.SCREENSHOT, params, timeout=30.0)
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    if resp.is_success:
        result = resp.result or {}
        if "image_base64" in result:
            return ImageContent(
                type="image",
                data=result["image_base64"],
                mimeType="image/png",
            )
        return {"status": "success", **result}
    return _blender_error_result(resp.error)


# ─── Tool 6: import_mesh ──────────────────────────────────────────────────────

@mcp.tool()
@_tool_dec()
async def import_mesh(
    filepath: str,
    format: str | None = None,
    scale: float = 1.0,
) -> dict[str, Any]:
    """
    Import a mesh file into the current Blender scene.

    Supports the FreeCAD-to-Blender handoff: parametric base geometry created
    in FreeCAD is imported here for organic detailing.

    Args:
        filepath: Path to the mesh file to import.
        format: One of "stl", "obj", "3mf", "step". Default: auto-detect from extension.
        scale: Scale factor applied on import. Default 1.0.

    Returns:
        imported_objects (list of names), total_vertices, total_faces, bounding_box.
    """
    valid_formats = ("stl", "obj", "3mf", "step", None)
    if format not in valid_formats:
        return {
            "status": "error",
            "error_code": ErrorCode.INVALID_PARAMS,
            "message": f"Invalid format {format!r}. Must be 'stl', 'obj', '3mf', 'step', or omit for auto-detect.",
        }

    try:
        conn = await _ensure_connected()
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    params: dict[str, Any] = {"filepath": filepath, "scale": scale}
    if format is not None:
        params["format"] = format

    try:
        resp = await conn.send_command(Command.IMPORT_MESH, params, timeout=30.0)
    except BlenderConnectionError as e:
        return _connection_error_result(e)

    if resp.is_success:
        return {"status": "success", **(resp.result or {})}
    return _blender_error_result(resp.error)


# ─── Tool 7: manage_connection ────────────────────────────────────────────────

@mcp.tool()
async def manage_connection(action: str) -> dict[str, Any]:
    """
    Diagnostic and lifecycle management for the Blender TCP connection.

    Args:
        action: One of:
                - "status": Return connection state, versions, health stats,
                            and per-tool performance report.
                - "reconnect": Disconnect and reconnect to Blender.
                - "ping": Measure round-trip latency (milliseconds).

    Returns:
        Depends on action. Status includes connected, blender_version,
        addon_version, protocol_version, uptime_seconds, health, performance.
    """
    if action not in ("status", "reconnect", "ping"):
        return {
            "status": "error",
            "error_code": ErrorCode.INVALID_PARAMS,
            "message": f"Invalid action {action!r}. Must be 'status', 'reconnect', or 'ping'.",
        }

    conn = _get_connection()

    if action == "status":
        st = conn.status()
        if not conn._connected:
            st["note"] = (
                "Not connected. Ensure Blender is running with the Blender MCP Bridge addon "
                "enabled, then call manage_connection(action='reconnect')."
            )
        # Append health and performance diagnostics
        monitor = get_monitor()
        if monitor:
            st["health"] = monitor.get_status()
        dbg = get_debugger()
        if dbg:
            st["performance"] = dbg.performance_report()
        return {"status": "success", **st}

    if action == "reconnect":
        monitor = get_monitor()
        try:
            result = await conn.reconnect()
            if monitor:
                monitor.record_reconnect_attempt(success=True)
                if not monitor._task or monitor._task.done():
                    monitor.start()
            return {"status": "success", **result}
        except BlenderConnectionError as e:
            if monitor:
                monitor.record_reconnect_attempt(success=False)
            return _connection_error_result(e)

    # action == "ping"
    if not conn._connected:
        try:
            await conn.connect()
        except BlenderConnectionError as e:
            return _connection_error_result(e)

    try:
        result = await conn.ping()
        return {"status": "success", **result}
    except BlenderConnectionError as e:
        return _connection_error_result(e)


# ─── Entrypoint ───────────────────────────────────────────────────────────────

def main() -> None:
    """Run the MCP server (stdio transport)."""
    dbg = get_debugger()
    if dbg:
        dbg.logger.info("Blender MCP server starting")
    else:
        logger.info("Blender MCP server starting (no file logging)")
    mcp.run()


if __name__ == "__main__":
    main()
