# Blender MCP — Agent Installation & Usage Guide

This file is for you, the AI agent. It tells you what needs to be true on this system for the Blender MCP server to work, how to make it true, and how to use it once it's running. Read it fully before taking any action.

## What This Is

blender-mcp is a Model Context Protocol (MCP) server providing 7 tools for controlling Blender — the open-source 3D modeling and rendering software. It's designed for organic and natural geometry: trees, human figures, terrain features, rock faces, streambeds, and anything else where Blender's mesh modeling excels. Once installed, you can generate and manipulate 3D models conversationally via `execute_blender_code`, inspect scenes, check printability, and export to STL/OBJ/3MF.

**Origin:** Built by one person for personal use, on a Mac, with Claude Code. Other platforms should work but are less tested.

## Architecture

```
AI Agent ──(MCP over stdio)── MCP Server ──(TCP :9876)── Blender Addon
```

There are two components:
1. **Blender addon** (`blender_mcp_bridge.py`) — a TCP server running inside Blender that executes commands on the main thread
2. **MCP server** (`blender_mcp` Python package) — translates MCP tool calls into TCP messages to the addon

Both must be installed. Blender must be running with the addon enabled for tools to work.

## Prerequisites

The following must be present on the system. Check each one. Install anything missing.

### 1. Blender 4.2+ (required)

Blender 4.2 or later (5.x is fine). The addon uses Blender's Python API which changed significantly at 4.x.

**Check:** Launch Blender and check the splash screen, or run `blender --version`.

**Install:**
- **macOS:** Download from https://www.blender.org/download/ — drag to Applications
- **Linux:** Download from https://www.blender.org/download/ (tar.xz), or `sudo snap install blender --classic`
- **Windows:** Installer from https://www.blender.org/download/

### 2. Python 3.10+ (required)

The MCP server runs on the system Python (not Blender's bundled Python).

**Check:** `python3 --version`

**Install:**
- **macOS:** `brew install python@3.12`
- **Linux:** `sudo apt install python3`
- **Windows:** https://www.python.org/downloads/

## Installation

### Step 1: Clone the repo

```bash
git clone https://github.com/blwfish/blender-mcp.git
```

Clone location: wherever repos live on this system. `~/blender-mcp` is a safe default.

### Step 2: Install the MCP server

```bash
cd blender-mcp
pip install -e ".[test]"
```

This installs the `blender-mcp` package with its dependencies (`mcp`, `fastmcp`).

### Step 3: Install the Blender addon

The addon is a single file: `addon/blender_mcp_bridge.py`. Install it into Blender:

1. Open Blender
2. Edit → Preferences → Add-ons → Install from Disk
3. Select `addon/blender_mcp_bridge.py` from the cloned repo
4. Enable the addon (checkbox)

The addon starts a TCP server automatically on `127.0.0.1:9876`. Verify in View3D → Sidebar (N) → Blender MCP tab — status should show "Listening".

**Note:** This step requires Blender's GUI. If you can manipulate Blender's addon directory directly, copy the file to Blender's addon path instead:
- **macOS:** `~/Library/Application Support/Blender/<version>/scripts/addons/`
- **Linux:** `~/.config/blender/<version>/scripts/addons/`
- **Windows:** `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\`

### Step 4: Register as an MCP Server

**Claude Code:**
```bash
claude mcp add blender -- python3 -m blender_mcp.server
```

**Other agents** — the server speaks standard MCP over stdio:
```json
{
  "mcpServers": {
    "blender": {
      "command": "python3",
      "args": ["-m", "blender_mcp.server"]
    }
  }
}
```

Config format varies by agent platform.

## Verify Installation

Ensure Blender is running with the addon enabled, then call:

```
manage_connection(action="status")
```

This should return connection info including Blender version and health stats. If it fails, check that Blender is running and the addon shows "Listening" in the sidebar panel.

A more thorough check:

```
get_scene_info(detail_level="summary")
```

This queries the Blender scene. If it returns object names and counts, everything is working.

## Environment Variables

All optional. Defaults work for standard setups.

| Variable | Purpose | Default |
|----------|---------|---------|
| `BLENDER_HOST` | TCP host for addon connection | `127.0.0.1` |
| `BLENDER_PORT` | TCP port for addon connection | `9876` |
| `BLENDERMCP_LOG_DIR` | Log file directory | `/tmp/blender_mcp_debug` |
| `BLENDERMCP_LOG_LEVEL` | `DEBUG` for verbose logging | `INFO` |

## How to Use the Tools

### Read CLAUDE.md First

The file `CLAUDE.md` in the repo root contains development notes, debug infrastructure details, and key invariants.

### Tools

| Tool | What it does |
|------|-------------|
| `execute_blender_code(code, timeout)` | Run Python in Blender's context. Access to `bpy` and all installed addons. This is your primary tool — most modeling happens here. |
| `get_scene_info(detail_level)` | Query scene: "summary" for names/types, "mesh" adds vertex/face counts, "full" adds materials and modifiers |
| `export_mesh(filepath, objects, format, scale, validate)` | Export to STL/OBJ/3MF. Scale applied at export only. |
| `check_mesh_printability(object_name, min_thickness, target_scale)` | Manifold check, thin features, volume — for 3D printing readiness |
| `screenshot(filepath, width, height)` | Capture the 3D viewport as PNG |
| `import_mesh(filepath, format, scale)` | Import STL/OBJ/3MF/STEP into the scene |
| `manage_connection(action)` | "status" for health/perf, "reconnect" to re-establish, "ping" for latency |

### Scale Convention

All modeling occurs at **full prototype dimensions** (meters). Scale is applied only at export:
- HO scale (1:87.1): `scale = 0.01148`
- This avoids floating-point precision issues with small geometry in Blender.

### Key Pattern

Most work happens through `execute_blender_code`. Write Python scripts that use `bpy` to create and manipulate geometry, then use the other tools for inspection, validation, and export:

```
execute_blender_code(code="import bpy; bpy.ops.mesh.primitive_cube_add(size=2)")
get_scene_info(detail_level="mesh")
check_mesh_printability(object_name="Cube")
export_mesh(filepath="/tmp/model.stl", scale=0.01148)
```

## Health and Debugging

| Symptom | What to do |
|---------|-----------|
| "Cannot connect to Blender" | Ensure Blender is running with addon enabled. Check sidebar panel shows "Listening". Check port 9876 is free. |
| "Protocol version mismatch" | Update the component identified in the error. Server and addon major.minor versions must match. |
| "Lost connection to Blender" | Call `manage_connection(action="reconnect")`. If Blender crashed, restart it. |
| Need to see what went wrong | Check log files in `/tmp/blender_mcp_debug/` or call `manage_connection(action="status")` for live health/perf data. |

### Log Files

The server writes structured logs to `$BLENDERMCP_LOG_DIR` (default `/tmp/blender_mcp_debug/`):

| File | Contents |
|------|----------|
| `blender_mcp.log` | MCP server — every tool call, errors with full tracebacks |
| `addon.log` | Blender addon — connect events, command errors |
| `operations_YYYYMMDD.json` | Newline-delimited JSON — one entry per call with timing |

## Contributing

### Filing Issues

Include: platform and version, Blender version, the tool call that failed, the complete error response, and relevant log files from `/tmp/blender_mcp_debug/`.

### Pull Requests

- The MCP server is in `src/blender_mcp/`; the addon is a single file in `addon/`
- Run `pytest tests/test_protocol.py tests/test_server.py tests/test_debug.py -v` before submitting (86 tests, no Blender required)
- Protocol: newline-delimited JSON with version in every message
- Never write to stdout from the MCP server (corrupts the stdio transport)
- Only call `bpy` from Blender's main thread via `bpy.app.timers`

## License

MIT
