# Changelog

All notable changes to this project will be documented in this file.
Semantic versioning: MAJOR.MINOR.PATCH

## [0.1.0] — 2026-02-20

Initial release.

### Added
- MCP server with 7 tools: `execute_blender_code`, `get_scene_info`, `export_mesh`,
  `check_mesh_printability`, `screenshot`, `import_mesh`, `manage_connection`
- Blender addon (`cova_mcp_bridge.py`) with TCP server, UI panel, and preferences
- Protocol v0.1.0: newline-delimited JSON with version in every message
- Protocol version compatibility checking on connect
- Defensive connection management: specific error codes for all failure modes
- Manifold validation and thin-feature analysis for resin printing
- HO scale (1:87.1) export pipeline
- Unit tests for protocol (no Blender required)
- Server tests with mock TCP Blender (no Blender required)
- Integration test scaffold (requires Blender with addon)
