"""
Blender MCP — Server-Side Validation Helpers

These helpers validate parameters and interpret printability results on the
MCP server side, without requiring Blender. Heavy mesh analysis is done in
the Blender addon; this module handles reporting and thresholds.
"""

from __future__ import annotations

from typing import Any

# ─── HO Scale Constants ───────────────────────────────────────────────────────

HO_SCALE = 1 / 87.1          # ≈ 0.01148
PROTOTYPE_UNITS = "meters"    # Blender scene units

# AnyCubic M7 Pro/Max resin printer thresholds (at target/HO scale, mm)
RESIN_WARN_MM   = 0.3   # below this: structural warning
RESIN_ERROR_MM  = 0.05  # below this: will not resolve at printer


def prototype_to_ho(value_m: float) -> float:
    """Convert prototype dimension (meters) to HO scale (mm)."""
    return value_m * HO_SCALE * 1000.0


def ho_to_prototype(value_mm: float) -> float:
    """Convert HO scale dimension (mm) to prototype (meters)."""
    return (value_mm / 1000.0) / HO_SCALE


# ─── Printability Report Interpretation ──────────────────────────────────────

def interpret_printability(result: dict[str, Any], target_scale: float = HO_SCALE) -> dict[str, Any]:
    """
    Add human-readable interpretation to a raw printability result from Blender.

    The addon returns raw numbers; this function adds:
    - severity classification for thin features
    - plain-English summary
    - recommended actions
    """
    issues: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    is_manifold = result.get("is_manifold", False)
    non_manifold_edges = result.get("non_manifold_edges", 0)
    non_manifold_verts = result.get("non_manifold_verts", 0)
    loose_geo = result.get("loose_geometry", {})
    degen_faces = result.get("degenerate_faces", 0)
    self_intersections = result.get("self_intersections", False)

    if not is_manifold:
        issues.append(
            f"Non-manifold geometry: {non_manifold_edges} edges, {non_manifold_verts} vertices. "
            "The mesh has holes or internal faces — will not print reliably."
        )
        recommendations.append(
            "Apply a Voxel Remesh (bpy.ops.object.modifier_add(type='REMESH')) "
            "or use 3D Print Toolbox > Make Manifold to fix."
        )

    loose_verts = loose_geo.get("vertices", 0)
    loose_edges = loose_geo.get("edges", 0)
    if loose_verts or loose_edges:
        issues.append(
            f"Loose geometry: {loose_verts} vertices, {loose_edges} edges not connected to faces. "
            "These will create stray fragments in the print."
        )
        recommendations.append(
            "Select mesh → Edit Mode → Mesh > Clean Up > Delete Loose."
        )

    if degen_faces:
        issues.append(
            f"{degen_faces} degenerate (zero-area) faces. May cause slicer errors."
        )
        recommendations.append(
            "Edit Mode → Mesh > Clean Up > Degenerate Dissolve."
        )

    if self_intersections:
        warnings.append(
            "Self-intersecting faces detected. Resin slicers typically handle these "
            "but CNC toolpaths may fail."
        )

    # Thin feature analysis
    thin_features = result.get("thin_features", [])
    for feat in thin_features:
        dim_mm = feat.get("min_dimension_scaled_mm", 0)
        if dim_mm < RESIN_ERROR_MM:
            issues.append(
                f"Feature at {feat.get('location', 'unknown')}: {dim_mm:.3f}mm at target scale "
                f"— below resin printer resolution ({RESIN_ERROR_MM}mm). Will not resolve."
            )
        elif dim_mm < RESIN_WARN_MM:
            warnings.append(
                f"Feature at {feat.get('location', 'unknown')}: {dim_mm:.3f}mm at target scale "
                f"— below recommended minimum ({RESIN_WARN_MM}mm). May be fragile."
            )

    # Summary
    printable = result.get("printable", False)
    if printable and not issues and not warnings:
        summary = "Mesh is print-ready."
    elif printable and warnings and not issues:
        summary = f"Mesh is print-ready with {len(warnings)} warning(s)."
    else:
        summary = (
            f"Mesh has {len(issues)} issue(s) that should be resolved before printing."
        )

    return {
        **result,
        "summary": summary,
        "issues": issues,
        "warnings": warnings,
        "recommendations": recommendations,
    }


# ─── Parameter Validation ────────────────────────────────────────────────────

def validate_export_params(
    filepath: str,
    format: str,
    scale: float,
    objects: list[str] | None,
) -> str | None:
    """Return an error string if parameters are invalid, else None."""
    if not filepath:
        return "filepath must not be empty"
    if format not in ("stl", "obj", "3mf"):
        return f"format must be 'stl', 'obj', or '3mf', got {format!r}"
    if scale <= 0:
        return f"scale must be positive, got {scale}"
    if objects is not None and len(objects) == 0:
        return "objects list is empty; omit it to export selected objects"
    return None
