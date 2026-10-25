"""Read the independent sca-physics namespace and track legacy compatibility.

Resolved values are copied from the product package. They are never inferred
from visual names, dimensions, or material labels.
"""

from copy import deepcopy


def _parameter(value, unit, detail):
    return {"value": value, "unit": unit,
            "source": {"kind": "default", "detail": detail}}


def legacy_settings(records, graph, config):
    """Explicit, reported compatibility policy for packages without metadata."""
    def parameter(value, unit):
        return _parameter(value, unit, "Legacy package has no sca_physics; exporter compatibility defaults")

    return {
        "schema_version": "1.0", "default_profile": "export-legacy-demo-v1",
        "scene": {"up_axis": parameter("+" + config.up_axis, "axis")},
        "parts": {
            record["node_id"]: {
                "definition_id": record["definition_id"],
                "contact": {
                    "static_friction": parameter(config.default_friction, "1"),
                    "dynamic_friction": parameter(config.default_dynamic_friction, "1"),
                    "restitution": parameter(config.default_restitution, "1"),
                },
                "collision": {
                    "enabled": parameter(True, "boolean"),
                    "approximation_dynamic": parameter(config.dynamic_collision_approximation, "token"),
                    "approximation_static": parameter(config.static_collision_approximation, "token"),
                },
            } for record in records if record["kind"] == "part"
        },
        "joints": {
            str(joint["joint_id"]): {
                "joint_type": str(joint["joint_type"]),
                "damping": parameter(0.0, "N*m*s/rad" if joint["joint_type"] == "revolute" else
                                     "N*s/m" if joint["joint_type"] == "prismatic" else "unsupported"),
                "collision_enabled": parameter(config.joint_collision_enabled, "boolean"),
            } for joint in graph.joints
        },
        "assembly": {"collision_exclusions": []},
    }


def package_settings(package, records, config, *, up_axis_override=None, explicit_config_fields=()):
    metadata = package.root_definition.metadata
    embedded = metadata.get("sca_physics")
    overrides = []
    if "sca_physics" not in metadata:
        settings = legacy_settings(records, package.occurrence_graph, config)
        origin = "legacy_exporter_defaults"
        warnings = ["Input package has no metadata.sca_physics. Exporter compatibility values are recorded in this report; run sca-physics enrich to persist them in the asset."]
        paths = {
            "up_axis": [settings["scene"]["up_axis"]],
            "default_friction": [p["contact"]["static_friction"] for p in settings["parts"].values()],
            "default_dynamic_friction": [p["contact"]["dynamic_friction"] for p in settings["parts"].values()],
            "default_restitution": [p["contact"]["restitution"] for p in settings["parts"].values()],
            "dynamic_collision_approximation": [p["collision"]["approximation_dynamic"] for p in settings["parts"].values()],
            "static_collision_approximation": [p["collision"]["approximation_static"] for p in settings["parts"].values()],
            "joint_collision_enabled": [j["collision_enabled"] for j in settings["joints"].values()],
        }
        for field in set(explicit_config_fields) & paths.keys():
            for parameter in paths[field]:
                parameter["source"] = {"kind": "explicit", "detail": "Explicit legacy exporter configuration: " + field}
    else:
        from .dependencies import require_physics
        require_physics()
        from sca_physics.contract import validate_config

        validate_config(package, embedded)
        settings = deepcopy(embedded)
        origin = "package.root_definition.metadata.sca_physics"
        warnings = []
        # Config files remain useful for tessellation and existing density/body
        # policies, but cannot accidentally replace the product's physics.
        forbidden = set(explicit_config_fields) & {
            "up_axis", "default_friction", "default_dynamic_friction",
            "default_restitution", "dynamic_collision_approximation",
            "static_collision_approximation", "joint_collision_enabled",
        }
        if forbidden:
            raise ValueError("Embedded physics takes precedence; update the product package instead of --config fields: " + ", ".join(sorted(forbidden)))
    if up_axis_override is not None:
        previous = deepcopy(settings["scene"]["up_axis"])
        replacement = {"value": "+" + up_axis_override, "unit": "axis",
                       "source": {"kind": "explicit", "detail": "User explicitly supplied USD --up-axis override"}}
        settings["scene"]["up_axis"] = replacement
        overrides.append({"field": "scene.up_axis", "before": previous, "after": replacement})
    return settings, {"origin": origin, "overrides": overrides, "warnings": warnings,
                      "resolved": deepcopy(settings)}
