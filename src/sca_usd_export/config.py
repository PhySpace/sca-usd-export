"""All demonstration defaults live here and are included in every report."""

from dataclasses import asdict, dataclass, fields
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class ExportConfig:
    # CAD coordinates remain unchanged in direction. This only labels USD up.
    # SCA's default scene uses +Z. Embedded package physics takes precedence.
    up_axis: str = "Z"
    # Explicit output units. CAD and the plugin's stored declarations stay SI.
    meters_per_unit: float = 1.0
    linear_deflection_mm: float = 0.1
    angular_deflection_degrees: float = 20.0
    relative_deflection: bool = False
    default_density_kg_m3: float = 1000.0
    unconnected_body_policy: str = "static"
    dynamic_collision_approximation: str = "convexHull"
    static_collision_approximation: str = "none"
    default_friction: float = 0.5
    default_dynamic_friction: float = 0.4
    default_restitution: float = 0.1
    gravity_m_s2: float = 9.81
    joint_collision_enabled: bool = False
    joint_limit_policy: str = "unbounded"
    geometry_absolute_tolerance_m: float = 1e-8

    def __post_init__(self):
        if isinstance(self.meters_per_unit, bool) or self.meters_per_unit not in (1.0, 0.01):
            raise ValueError('meters_per_unit must be 1.0 (legacy metres) or 0.01 (centimetres)')
        if self.up_axis not in ("Y", "Z"):
            raise ValueError("up_axis must be Y or Z; coordinates are not rotated")
        for name in (
            "linear_deflection_mm",
            "angular_deflection_degrees",
            "default_density_kg_m3",
            "geometry_absolute_tolerance_m",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive and finite")
        if self.angular_deflection_degrees > 180:
            raise ValueError("angular_deflection_degrees must be <= 180")
        for name in ("relative_deflection", "joint_collision_enabled"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be bool")
        for name in ("default_friction", "default_dynamic_friction", "default_restitution", "gravity_m_s2"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be nonnegative and finite")
        if self.default_restitution > 1:
            raise ValueError("default_restitution must be <= 1")
        if self.unconnected_body_policy not in ("static", "dynamic"):
            raise ValueError("unconnected_body_policy must be static or dynamic")
        if self.dynamic_collision_approximation not in (
            "convexHull",
            "boundingCube",
            "boundingSphere",
        ):
            raise ValueError(
                "dynamic_collision_approximation must be convexHull, boundingCube or boundingSphere"
            )
        if self.static_collision_approximation not in (
            "none",
            "convexHull",
            "boundingCube",
            "boundingSphere",
        ):
            raise ValueError("invalid static_collision_approximation")
        if self.joint_limit_policy not in ("unbounded", "error"):
            raise ValueError("joint_limit_policy must be unbounded or error")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_json(cls, path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("configuration must be a JSON object")
        unknown = set(payload) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown configuration fields: {sorted(unknown)}")
        return cls(**payload)
