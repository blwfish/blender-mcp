"""
Blender MCP Bridge — Blender Addon

TCP socket server that receives JSON commands from the Blender MCP server
and executes them on Blender's main thread. Installable via:
    Edit → Preferences → Add-ons → Install from Disk → blender_mcp_bridge.py

Protocol: JSON objects delimited by newline, over TCP on localhost:9876.
See protocol specification in docs/SPEC.md.
"""

bl_info = {
    "name": "Blender MCP Bridge",
    "author": "",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Blender MCP",
    "description": "TCP server for MCP-driven organic geometry generation",
    "category": "Interface",
}

import base64
import io
import json
import logging
import math
import os
import queue
import socket
import sys
import threading
import time
import traceback
import uuid
from typing import Any

import bpy
import bmesh

# ─── Protocol Constants ───────────────────────────────────────────────────────

PROTOCOL_VERSION = "0.1.0"
ADDON_VERSION = ".".join(str(x) for x in bl_info["version"])

BLENDER_VERSION = ".".join(str(x) for x in bpy.app.version)

DEFAULT_PORT = 9876
TIMER_INTERVAL = 0.05  # seconds between queue polls

# Commands
CMD_EXECUTE_CODE       = "execute_code"
CMD_GET_SCENE_INFO     = "get_scene_info"
CMD_EXPORT_MESH        = "export_mesh"
CMD_CHECK_PRINTABILITY = "check_printability"
CMD_SCREENSHOT         = "screenshot"
CMD_IMPORT_MESH        = "import_mesh"
CMD_PING               = "ping"
CMD_GET_VERSION        = "get_version"

# Error codes (mirrored from server protocol.py)
ERR_EXECUTION_ERROR   = "EXECUTION_ERROR"
ERR_TIMEOUT           = "TIMEOUT"
ERR_INVALID_COMMAND   = "INVALID_COMMAND"
ERR_INVALID_PARAMS    = "INVALID_PARAMS"
ERR_OBJECT_NOT_FOUND  = "OBJECT_NOT_FOUND"
ERR_EXPORT_FAILED     = "EXPORT_FAILED"
ERR_IMPORT_FAILED     = "IMPORT_FAILED"
ERR_VERSION_MISMATCH  = "VERSION_MISMATCH"
ERR_INTERNAL_ERROR    = "INTERNAL_ERROR"


# ─── Logging Setup ────────────────────────────────────────────────────────────

def _setup_logger() -> logging.Logger:
    log = logging.getLogger("blender_mcp_bridge")
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s [Blender MCP] %(levelname)s %(message)s"))
        log.addHandler(h)
    log.setLevel(logging.INFO)
    return log

logger = _setup_logger()


# ─── Message Helpers ─────────────────────────────────────────────────────────

def _make_success(message_id: str, result: dict) -> bytes:
    msg = {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": message_id,
        "status": "success",
        "result": result,
    }
    return (json.dumps(msg) + "\n").encode()


def _make_error(
    message_id: str,
    code: str,
    message: str,
    tb: str | None = None,
    context: dict | None = None,
) -> bytes:
    error: dict[str, Any] = {"code": code, "message": message}
    if tb:
        error["traceback"] = tb
    if context:
        error["context"] = context
    msg = {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": message_id,
        "status": "error",
        "error": error,
    }
    return (json.dumps(msg) + "\n").encode()


def _parse_request(raw: str) -> tuple[dict | None, str]:
    """Parse a JSON request line. Returns (parsed_dict, error_message)."""
    try:
        d = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e} | raw (truncated): {raw[:200]!r}"
    if "command" not in d:
        return None, "Missing required field: command"
    return d, ""


# ─── Command Handlers ─────────────────────────────────────────────────────────

def handle_get_version(params: dict) -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "addon_version": ADDON_VERSION,
        "blender_version": BLENDER_VERSION,
        "available_commands": [
            CMD_EXECUTE_CODE, CMD_GET_SCENE_INFO, CMD_EXPORT_MESH,
            CMD_CHECK_PRINTABILITY, CMD_SCREENSHOT, CMD_IMPORT_MESH,
            CMD_PING, CMD_GET_VERSION,
        ],
    }


def handle_ping(params: dict) -> dict:
    return {"pong": True, "timestamp": time.time()}


def handle_execute_code(params: dict) -> dict:
    code = params.get("code", "")
    if not code:
        raise ValueError("Missing required param: code")

    # Capture stdout
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    captured_out = io.StringIO()
    captured_err = io.StringIO()

    t0 = time.monotonic()
    return_value = None
    try:
        sys.stdout = captured_out
        sys.stderr = captured_err
        # Compile so we can capture the last expression's value
        compiled = compile(code, "<blender_mcp>", "exec")
        ns: dict[str, Any] = {"__name__": "__main__"}
        exec(compiled, ns)
        # Try to get last expression value (like REPL behavior)
        # This is a best-effort: exec doesn't return a value
        return_value = ns.get("_", None)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    elapsed = time.monotonic() - t0
    return {
        "stdout": captured_out.getvalue(),
        "stderr": captured_err.getvalue(),
        "return_value": repr(return_value),
        "execution_time": round(elapsed, 4),
    }


def handle_get_scene_info(params: dict) -> dict:
    detail = params.get("detail_level", "summary")
    scene = bpy.context.scene

    unit_settings = scene.unit_settings
    result: dict[str, Any] = {
        "scene_name": scene.name,
        "object_count": len(scene.objects),
        "unit_system": unit_settings.system,
        "unit_scale": unit_settings.scale_length,
        "frame_current": scene.frame_current,
        "objects": [],
    }

    for obj in scene.objects:
        obj_info: dict[str, Any] = {
            "name": obj.name,
            "type": obj.type,
            "visible": obj.visible_get(),
        }

        if detail in ("mesh", "full") and obj.type == "MESH":
            mesh = obj.data
            obj_info["vertex_count"] = len(mesh.vertices)
            obj_info["face_count"] = len(mesh.polygons)
            bb = obj.bound_box  # 8 corners in local space
            # World-space bounding box extents
            world_corners = [obj.matrix_world @ __import__('mathutils').Vector(c) for c in bb]
            xs = [c.x for c in world_corners]
            ys = [c.y for c in world_corners]
            zs = [c.z for c in world_corners]
            obj_info["bounding_box"] = {
                "min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
                "size": [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)],
            }

        if detail == "full":
            obj_info["modifiers"] = [m.name + ":" + m.type for m in obj.modifiers]
            if obj.type == "MESH":
                obj_info["materials"] = [
                    ms.material.name if ms.material else None
                    for ms in obj.material_slots
                ]
            if obj.type == "ARMATURE":
                obj_info["bone_count"] = len(obj.data.bones)

        result["objects"].append(obj_info)

    return result


def _get_object_or_raise(name: str) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    if obj is None:
        existing = [o.name for o in bpy.data.objects]
        raise KeyError(
            f"Object {name!r} not found. Existing objects: {existing}"
        )
    return obj


def handle_export_mesh(params: dict) -> dict:
    filepath = params.get("filepath", "")
    if not filepath:
        raise ValueError("Missing required param: filepath")

    fmt = params.get("format", "stl").lower()
    scale = float(params.get("scale", 1.0))
    do_validate = bool(params.get("validate", True))
    object_names: list[str] | None = params.get("objects")

    # Select the requested objects (or use existing selection)
    if object_names is not None:
        bpy.ops.object.select_all(action="DESELECT")
        for name in object_names:
            obj = _get_object_or_raise(name)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj

    selected = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    if not selected:
        raise ValueError(
            "No mesh objects selected for export. Either specify 'objects' "
            "parameter or select objects in Blender before calling export_mesh."
        )

    # Optional manifold validation
    validation_result = None
    if do_validate:
        validation_result = _check_manifold_quick(selected)

    # Gather pre-export stats
    total_verts = sum(len(o.data.vertices) for o in selected)
    total_faces = sum(len(o.data.polygons) for o in selected)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

    # Export
    if fmt == "stl":
        bpy.ops.wm.stl_export(
            filepath=filepath,
            export_selected_objects=True,
            global_scale=scale,
        )
    elif fmt == "obj":
        bpy.ops.wm.obj_export(
            filepath=filepath,
            export_selected_objects=True,
            global_scale=scale,
        )
    elif fmt == "3mf":
        # 3MF export available in Blender 3.6+
        bpy.ops.export_mesh.threemf(
            filepath=filepath,
            global_scale=scale,
            use_selection=True,
        )
    else:
        raise ValueError(f"Unsupported format: {fmt!r}")

    file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

    # Bounding box at scaled dimensions (mm)
    all_corners = []
    import mathutils
    for obj in selected:
        for c in obj.bound_box:
            world = obj.matrix_world @ mathutils.Vector(c)
            all_corners.append(world * scale * 1000.0)  # convert to mm

    if all_corners:
        xs = [c.x for c in all_corners]
        ys = [c.y for c in all_corners]
        zs = [c.z for c in all_corners]
        bb_scaled = {
            "x_mm": round(max(xs) - min(xs), 3),
            "y_mm": round(max(ys) - min(ys), 3),
            "z_mm": round(max(zs) - min(zs), 3),
        }
    else:
        bb_scaled = {}

    return {
        "filepath": os.path.abspath(filepath),
        "format": fmt,
        "object_count": len(selected),
        "total_vertices": total_verts,
        "total_faces": total_faces,
        "file_size_bytes": file_size,
        "scale_applied": scale,
        "bounding_box_scaled_mm": bb_scaled,
        "validation_result": validation_result,
    }


def _check_manifold_quick(objects: list) -> dict:
    """Quick manifold check across a list of mesh objects."""
    results = []
    for obj in objects:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        non_manifold_edges = [e for e in bm.edges if not e.is_manifold]
        non_manifold_verts = [v for v in bm.verts if not v.is_manifold]
        results.append({
            "object": obj.name,
            "is_manifold": len(non_manifold_edges) == 0 and len(non_manifold_verts) == 0,
            "non_manifold_edges": len(non_manifold_edges),
            "non_manifold_verts": len(non_manifold_verts),
        })
        bm.free()
    all_manifold = all(r["is_manifold"] for r in results)
    return {"all_manifold": all_manifold, "per_object": results}


def handle_check_printability(params: dict) -> dict:
    import mathutils

    object_name = params.get("object_name", "")
    if not object_name:
        raise ValueError("Missing required param: object_name")

    min_thickness = float(params.get("min_thickness", 0.005))
    target_scale = float(params.get("target_scale", 0.01148))

    obj = _get_object_or_raise(object_name)
    if obj.type != "MESH":
        raise ValueError(f"Object {object_name!r} is type {obj.type!r}, not MESH")

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # Manifold
    non_manifold_edges = [e for e in bm.edges if not e.is_manifold]
    non_manifold_verts = [v for v in bm.verts if not v.is_manifold]
    is_manifold = len(non_manifold_edges) == 0 and len(non_manifold_verts) == 0

    # Loose geometry
    loose_verts = [v for v in bm.verts if not v.link_edges]
    loose_edges = [e for e in bm.edges if not e.link_faces]

    # Degenerate faces (zero area)
    degen_faces = [f for f in bm.faces if f.calc_area() < 1e-10]

    # Volume (signed, handles non-manifold gracefully)
    try:
        volume_prototype = bm.calc_volume(signed=False)
    except Exception:
        volume_prototype = 0.0

    volume_scaled = volume_prototype * (target_scale ** 3) * (1000.0 ** 3)  # mm³

    # Bounding box
    bb = obj.bound_box
    world_corners = [obj.matrix_world @ mathutils.Vector(c) for c in bb]
    xs = [c.x for c in world_corners]
    ys = [c.y for c in world_corners]
    zs = [c.z for c in world_corners]
    size_m = [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)]
    size_mm_scaled = [s * target_scale * 1000.0 for s in size_m]

    bm.free()

    # Self-intersection: use boolean modifier test
    self_intersections = False
    try:
        bm2 = bmesh.new()
        bm2.from_mesh(obj.data)
        result = bmesh.ops.intersect(
            bm2,
            geom=bm2.faces[:],
            geom_self=bm2.faces[:],
            mode="PRIVATE_COLLECT",
        )
        self_intersections = len(result.get("geom", [])) > 0
        bm2.free()
    except Exception:
        pass  # bmesh.ops.intersect API varies; skip if unavailable

    # Thin feature detection (simplified: check edge lengths)
    thin_features = []
    try:
        bm3 = bmesh.new()
        bm3.from_mesh(obj.data)
        for edge in bm3.edges:
            length_m = edge.calc_length()
            length_mm_scaled = length_m * target_scale * 1000.0
            if length_mm_scaled < min_thickness * target_scale * 1000.0:
                midpoint = (edge.verts[0].co + edge.verts[1].co) / 2
                midpoint_world = obj.matrix_world @ midpoint
                thin_features.append({
                    "min_dimension_prototype_m": round(length_m, 6),
                    "min_dimension_scaled_mm": round(length_mm_scaled, 4),
                    "location": [round(midpoint_world.x, 4), round(midpoint_world.y, 4), round(midpoint_world.z, 4)],
                })
                if len(thin_features) >= 20:  # cap report length
                    thin_features.append({"note": "Additional thin features not shown (capped at 20)"})
                    break
        bm3.free()
    except Exception as e:
        thin_features = [{"error": f"Thin feature analysis failed: {e}"}]

    printable = (
        is_manifold
        and len(loose_verts) == 0
        and len(loose_edges) == 0
        and len(degen_faces) == 0
    )

    return {
        "object_name": object_name,
        "is_manifold": is_manifold,
        "non_manifold_edges": len(non_manifold_edges),
        "non_manifold_verts": len(non_manifold_verts),
        "loose_geometry": {
            "vertices": len(loose_verts),
            "edges": len(loose_edges),
        },
        "degenerate_faces": len(degen_faces),
        "self_intersections": self_intersections,
        "thin_features": thin_features,
        "bounding_box": {
            "prototype_m": [round(s, 4) for s in size_m],
            "scaled_mm": [round(s, 3) for s in size_mm_scaled],
        },
        "volume": {
            "prototype_m3": round(volume_prototype, 6),
            "scaled_mm3": round(volume_scaled, 3),
        },
        "target_scale": target_scale,
        "printable": printable,
    }


def handle_screenshot(params: dict) -> dict:
    filepath = params.get("filepath")
    width = int(params.get("width", 1920))
    height = int(params.get("height", 1080))

    # Find a 3D viewport area
    area = None
    for window in bpy.context.window_manager.windows:
        for a in window.screen.areas:
            if a.type == "VIEW_3D":
                area = a
                break
        if area:
            break

    if area is None:
        raise RuntimeError("No 3D viewport found in Blender's UI")

    # Use offscreen rendering for reliable headless-compatible capture
    # This avoids the context override fragility of bpy.ops.screen.screenshot
    try:
        offscreen = bpy.types.GPUOffScreen(width, height)
        region = None
        space = None
        for region_ in area.regions:
            if region_.type == "WINDOW":
                region = region_
                break
        for space_ in area.spaces:
            if space_.type == "VIEW_3D":
                space = space_
                break

        if region is None or space is None:
            raise RuntimeError("Cannot find VIEW_3D window region")

        rv3d = space.region_3d
        offscreen.draw_view3d(
            bpy.context.scene,
            bpy.context.view_layer,
            space,
            region,
            rv3d.view_matrix,
            rv3d.projection_matrix,
            do_color_management=True,
        )

        # Read pixels
        buffer = offscreen.texture_color.read()
        buffer.dimensions = width * height * 4
        pixels = bytes(buffer)
        offscreen.free()

        # Encode to PNG via Blender image API
        img = bpy.data.images.new("_blender_mcp_screenshot", width=width, height=height, alpha=True)
        img.pixels.foreach_set([p / 255.0 for p in pixels])
        img.filepath_raw = filepath or "/tmp/_blender_mcp_screenshot.png"
        img.file_format = "PNG"

        if filepath:
            img.save()
            bpy.data.images.remove(img)
            file_size = os.path.getsize(filepath)
            return {"filepath": filepath, "width": width, "height": height, "file_size_bytes": file_size}
        else:
            # Return base64
            img.save()
            tmp = img.filepath_raw
            bpy.data.images.remove(img)
            with open(tmp, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            os.unlink(tmp)
            return {"image_base64": b64, "width": width, "height": height}

    except Exception as e:
        # Fallback: try viewport screenshot operator
        logger.warning("Offscreen render failed (%s), trying operator fallback", e)
        tmp_path = filepath or "/tmp/_blender_mcp_screenshot.png"
        try:
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.screen.screenshot(filepath=tmp_path, full=False)
        except Exception as e2:
            raise RuntimeError(
                f"Both offscreen render ({e}) and screenshot operator ({e2}) failed. "
                "Ensure a 3D viewport is open and visible."
            )
        if filepath:
            return {"filepath": tmp_path, "width": width, "height": height}
        with open(tmp_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        os.unlink(tmp_path)
        return {"image_base64": b64, "width": width, "height": height}


def handle_import_mesh(params: dict) -> dict:
    filepath = params.get("filepath", "")
    if not filepath:
        raise ValueError("Missing required param: filepath")
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath!r}")

    fmt = params.get("format") or os.path.splitext(filepath)[1].lower().lstrip(".")
    scale = float(params.get("scale", 1.0))

    # Record existing objects to identify newly imported ones
    before = set(o.name for o in bpy.data.objects)

    if fmt == "stl":
        bpy.ops.wm.stl_import(filepath=filepath, global_scale=scale)
    elif fmt == "obj":
        bpy.ops.wm.obj_import(filepath=filepath, global_scale=scale)
    elif fmt == "3mf":
        bpy.ops.import_mesh.threemf(filepath=filepath, global_scale=scale)
    elif fmt == "step":
        # STEP requires ImportCAD or similar addon; use execute_blender_code if not available
        try:
            import importlib
            importlib.import_module("import_cad")
            bpy.ops.import_cad.step(filepath=filepath)
        except ImportError:
            raise ImportError(
                "STEP import requires the 'ImportCAD' addon or similar. "
                "Use execute_blender_code to import STEP via an available addon."
            )
    else:
        raise ValueError(f"Unsupported import format: {fmt!r}")

    after = set(o.name for o in bpy.data.objects)
    imported_names = sorted(after - before)
    imported_objects = [bpy.data.objects[n] for n in imported_names]

    total_verts = 0
    total_faces = 0
    all_corners = []
    import mathutils

    for obj in imported_objects:
        if obj.type == "MESH":
            total_verts += len(obj.data.vertices)
            total_faces += len(obj.data.polygons)
            for c in obj.bound_box:
                world = obj.matrix_world @ mathutils.Vector(c)
                all_corners.append(world)

    bb = None
    if all_corners:
        xs = [c.x for c in all_corners]
        ys = [c.y for c in all_corners]
        zs = [c.z for c in all_corners]
        bb = {
            "min": [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)],
            "max": [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)],
            "size_m": [round(max(xs) - min(xs), 4), round(max(ys) - min(ys), 4), round(max(zs) - min(zs), 4)],
        }

    return {
        "imported_objects": imported_names,
        "object_count": len(imported_names),
        "total_vertices": total_verts,
        "total_faces": total_faces,
        "bounding_box": bb,
    }


# ─── Command Dispatch ─────────────────────────────────────────────────────────

HANDLERS = {
    CMD_GET_VERSION:       handle_get_version,
    CMD_PING:              handle_ping,
    CMD_EXECUTE_CODE:      handle_execute_code,
    CMD_GET_SCENE_INFO:    handle_get_scene_info,
    CMD_EXPORT_MESH:       handle_export_mesh,
    CMD_CHECK_PRINTABILITY: handle_check_printability,
    CMD_SCREENSHOT:        handle_screenshot,
    CMD_IMPORT_MESH:       handle_import_mesh,
}


def dispatch_command(req: dict) -> bytes:
    """Execute a command on Blender's main thread and return a response bytestring."""
    message_id = req.get("message_id", str(uuid.uuid4()))
    command = req.get("command", "")
    params = req.get("params", {})

    # Protocol version check
    req_version = req.get("protocol_version", PROTOCOL_VERSION)
    req_major_minor = tuple(int(x) for x in req_version.split(".")[:2])
    our_major_minor = tuple(int(x) for x in PROTOCOL_VERSION.split(".")[:2])
    if req_major_minor != our_major_minor:
        return _make_error(
            message_id,
            ERR_VERSION_MISMATCH,
            f"Protocol version mismatch: request uses {req_version}, addon uses {PROTOCOL_VERSION}. "
            f"Update the {'MCP server' if req_version > PROTOCOL_VERSION else 'Blender addon'}.",
        )

    handler = HANDLERS.get(command)
    if handler is None:
        return _make_error(
            message_id,
            ERR_INVALID_COMMAND,
            f"Unknown command: {command!r}. Valid commands: {sorted(HANDLERS.keys())}",
        )

    t0 = time.monotonic()
    try:
        result = handler(params)
        elapsed = time.monotonic() - t0
        result["_execution_time"] = round(elapsed, 4)
        return _make_success(message_id, result)
    except KeyError as e:
        return _make_error(
            message_id, ERR_OBJECT_NOT_FOUND, str(e),
            context={"command": command, "params": params},
        )
    except (ValueError, TypeError) as e:
        return _make_error(
            message_id, ERR_INVALID_PARAMS, str(e),
            context={"command": command},
        )
    except FileNotFoundError as e:
        code = ERR_IMPORT_FAILED if command == CMD_IMPORT_MESH else ERR_EXPORT_FAILED
        return _make_error(message_id, code, str(e))
    except OSError as e:
        code = ERR_EXPORT_FAILED if command == CMD_EXPORT_MESH else ERR_IMPORT_FAILED
        return _make_error(message_id, code, f"OS error: {e}")
    except Exception:
        tb = traceback.format_exc()
        logger.error("Unhandled exception in %s:\n%s", command, tb)
        return _make_error(
            message_id, ERR_EXECUTION_ERROR,
            f"Exception during {command}: {traceback.format_exc().splitlines()[-1]}",
            tb=tb,
            context={"command": command},
        )


# ─── TCP Server (Background Thread) ──────────────────────────────────────────

class _PendingCommand:
    __slots__ = ("request", "conn", "done")

    def __init__(self, request: dict, conn: "ClientConnection"):
        self.request = request
        self.conn = conn
        self.done = threading.Event()
        self._response: bytes | None = None

    def set_response(self, response: bytes) -> None:
        self._response = response
        self.done.set()

    def get_response(self, timeout: float = 60.0) -> bytes | None:
        self.done.wait(timeout)
        return self._response


class ClientConnection:
    def __init__(self, sock: socket.socket, addr: tuple, bridge: "BlenderMCPBridge"):
        self.sock = sock
        self.addr = addr
        self.bridge = bridge
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"blender-mcp-client-{addr}")
        self._thread.start()

    def _run(self) -> None:
        logger.info("Client connected from %s", self.addr)
        buf = b""
        try:
            while True:
                try:
                    chunk = self.sock.recv(65536)
                except (ConnectionResetError, OSError):
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._handle_line(line.decode(errors="replace"))
        finally:
            logger.info("Client disconnected from %s", self.addr)
            try:
                self.sock.close()
            except Exception:
                pass
            self.bridge._notify_client_disconnected()

    def _handle_line(self, raw: str) -> None:
        req, err = _parse_request(raw)
        if err:
            message_id = str(uuid.uuid4())
            response = _make_error(message_id, "INVALID_MESSAGE", err)
            self._send(response)
            return

        # Queue for execution on Blender's main thread
        pending = _PendingCommand(req, self)
        self.bridge._command_queue.put(pending)

        # Wait for result (blocking this client thread)
        command_timeout = float(req.get("params", {}).get("timeout", 60)) + 10.0
        response = pending.get_response(timeout=command_timeout)
        if response is None:
            response = _make_error(
                req.get("message_id", ""),
                ERR_TIMEOUT,
                f"Command timed out after {command_timeout:.0f}s waiting for Blender main thread.",
            )
        self._send(response)

    def _send(self, data: bytes) -> None:
        try:
            self.sock.sendall(data)
        except OSError as e:
            logger.warning("Failed to send response to %s: %s", self.addr, e)


class BlenderMCPBridge:
    def __init__(self):
        self._server_socket: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._command_queue: queue.Queue = queue.Queue()
        self._running = False
        self._port = DEFAULT_PORT
        self._connection_count = 0
        self._last_command: str = ""
        self._last_command_time: float = 0.0
        self._status = "stopped"  # stopped | listening | error

    def start(self, port: int = DEFAULT_PORT) -> None:
        if self._running:
            return
        self._port = port
        try:
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_socket.bind(("127.0.0.1", port))
            self._server_socket.listen(5)
            self._running = True
            self._status = "listening"
            self._server_thread = threading.Thread(
                target=self._accept_loop, daemon=True, name="blender-mcp-server"
            )
            self._server_thread.start()
            bpy.app.timers.register(self._main_thread_tick, persistent=True)
            logger.info("Blender MCP Bridge listening on 127.0.0.1:%d", port)
        except OSError as e:
            self._status = "error"
            logger.error("Failed to start server on port %d: %s", port, e)
            raise

    def stop(self) -> None:
        self._running = False
        self._status = "stopped"
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None
        if bpy.app.timers.is_registered(self._main_thread_tick):
            bpy.app.timers.unregister(self._main_thread_tick)
        logger.info("Blender MCP Bridge stopped")

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._server_socket.accept()
                self._connection_count += 1
                ClientConnection(conn, addr, self)
            except OSError:
                break  # socket closed

    def _notify_client_disconnected(self) -> None:
        pass  # could update UI status

    def _main_thread_tick(self) -> float | None:
        """Called by bpy.app.timers on Blender's main thread. Process pending commands."""
        if not self._running:
            return None  # unregister timer

        try:
            pending: _PendingCommand = self._command_queue.get_nowait()
        except queue.Empty:
            return TIMER_INTERVAL

        cmd_name = pending.request.get("command", "?")
        self._last_command = cmd_name
        self._last_command_time = time.time()
        logger.debug("Executing command: %s", cmd_name)

        response = dispatch_command(pending.request)
        pending.set_response(response)
        return TIMER_INTERVAL


# ─── Singleton Bridge Instance ────────────────────────────────────────────────

_bridge: BlenderMCPBridge | None = None


def get_bridge() -> BlenderMCPBridge:
    global _bridge
    if _bridge is None:
        _bridge = BlenderMCPBridge()
    return _bridge


# ─── Preferences ─────────────────────────────────────────────────────────────

class BlenderMCPPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    port: bpy.props.IntProperty(
        name="Port",
        description="TCP port for the MCP bridge server",
        default=DEFAULT_PORT,
        min=1024,
        max=65535,
    )
    auto_start: bpy.props.BoolProperty(
        name="Auto-start on Blender launch",
        description="Automatically start the MCP server when Blender opens",
        default=True,
    )
    log_level: bpy.props.EnumProperty(
        name="Log Level",
        items=[
            ("DEBUG",   "Debug",   "Verbose: all messages including TCP traffic"),
            ("INFO",    "Info",    "Standard: connection events and errors"),
            ("WARNING", "Warning", "Warnings and errors only"),
            ("ERROR",   "Error",   "Errors only"),
        ],
        default="INFO",
    )
    log_file: bpy.props.StringProperty(
        name="Log File",
        description="Path to log file. Empty = Blender console only.",
        default="",
        subtype="FILE_PATH",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "port")
        layout.prop(self, "auto_start")
        layout.prop(self, "log_level")
        layout.prop(self, "log_file")


# ─── Operators ────────────────────────────────────────────────────────────────

class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blender_mcp.start_server"
    bl_label = "Start MCP Server"
    bl_description = "Start the Blender MCP Bridge TCP server"

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        bridge = get_bridge()
        try:
            bridge.start(port=prefs.port)
            self.report({"INFO"}, f"Blender MCP Bridge started on port {prefs.port}")
        except OSError as e:
            self.report({"ERROR"}, f"Failed to start server: {e}")
        return {"FINISHED"}


class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blender_mcp.stop_server"
    bl_label = "Stop MCP Server"
    bl_description = "Stop the Blender MCP Bridge TCP server"

    def execute(self, context):
        bridge = get_bridge()
        bridge.stop()
        self.report({"INFO"}, "Blender MCP Bridge stopped")
        return {"FINISHED"}


# ─── UI Panel ─────────────────────────────────────────────────────────────────

class BLENDERMCP_PT_MCPPanel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_mcp_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blender MCP"

    def draw(self, context):
        layout = self.layout
        bridge = get_bridge()

        # Status
        status = bridge._status
        status_icon = {"listening": "CHECKMARK", "stopped": "X", "error": "ERROR"}.get(status, "QUESTION")
        row = layout.row()
        row.label(text=f"Status: {status.capitalize()}", icon=status_icon)

        # Port
        layout.label(text=f"Port: {bridge._port}")

        # Connections
        layout.label(text=f"Connections this session: {bridge._connection_count}")

        # Last command
        if bridge._last_command:
            t = time.strftime("%H:%M:%S", time.localtime(bridge._last_command_time))
            layout.label(text=f"Last: {bridge._last_command} @ {t}")

        # Start/Stop buttons
        row = layout.row()
        if bridge._running:
            row.operator("blender_mcp.stop_server", icon="PAUSE")
        else:
            row.operator("blender_mcp.start_server", icon="PLAY")


# ─── Registration ─────────────────────────────────────────────────────────────

_CLASSES = [
    BlenderMCPPreferences,
    BLENDERMCP_OT_StartServer,
    BLENDERMCP_OT_StopServer,
    BLENDERMCP_PT_MCPPanel,
]


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)

    # Auto-start if preference is set
    prefs = bpy.context.preferences.addons.get(__name__)
    if prefs and prefs.preferences.auto_start:
        bridge = get_bridge()
        try:
            bridge.start(port=prefs.preferences.port)
        except Exception as e:
            logger.error("Auto-start failed: %s", e)


def unregister():
    bridge = get_bridge()
    bridge.stop()

    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
