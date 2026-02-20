# COVA MCP Bridge — Blender Addon

Single-file Blender addon that runs a TCP socket server, enabling the COVA Blender MCP server to drive Blender remotely.

## Installation

1. Open Blender
2. Edit → Preferences → Add-ons → Install from Disk
3. Select `cova_mcp_bridge.py` (this file)
4. Check the checkbox to enable it

The server starts automatically (configurable in preferences).

## UI

View3D → Sidebar (N key) → COVA MCP tab:
- Status: Listening / Stopped / Error
- Port: 9876 (default, configurable in preferences)
- Connection count and last command for diagnostics
- Start/Stop button

## Configuration

Edit → Preferences → Add-ons → COVA Blender MCP Bridge:
- **Port**: TCP listen port (default 9876)
- **Auto-start on Blender launch**: Start server when addon loads
- **Log Level**: DEBUG / INFO / WARNING / ERROR
- **Log File**: Optional file path for logs (empty = Blender console only)

## Protocol

Speaks the COVA MCP TCP protocol: newline-delimited JSON with protocol version `0.1.0`.
The addon has no knowledge of MCP — it speaks only TCP/JSON, and can be driven by any TCP client for testing.

## Dependencies

None beyond Blender itself. Uses only Python standard library and `bpy`.

## Security

The server binds to `127.0.0.1` only (never `0.0.0.0`). No network exposure.
Code execution runs in Blender's `__main__` namespace — the same trust level as Blender's built-in Python console.
