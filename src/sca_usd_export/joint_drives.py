"""Ordinary-joint force drives and the pinned PhysX 107.3 articulation fields.

Author in kg/metre/degree USD units, then let stage_units convert the layer.
Runtime commands only update already exported drives; they never create APIs.
"""
from __future__ import annotations

import math


def author_drive(prim, kind, settings):
    from pxr import Sdf, UsdPhysics
    angular = kind == "revolute"
    name = "angular" if angular else "linear"
    gain = math.pi / 180 if angular else 1.0
    coordinate = 180 / math.pi if angular else 1.0
    passive = float(settings["damping"]["value"])
    declaration = settings.get("drive")
    mode = declaration["mode"]["value"] if declaration else "off"
    active = mode != "off"
    stiffness = declaration["stiffness"]["value"] if active else 0.0
    damping = declaration["damping"]["value"] if active else 0.0
    total = passive + damping
    velocity_weight = damping / total if total else 0.0
    position = declaration["target_position"]["value"] if active else 0.0
    velocity = declaration["target_velocity"]["value"] * velocity_weight if active else 0.0
    drive = UsdPhysics.DriveAPI.Apply(prim, name)
    values = {"type": "force", "stiffness": stiffness * gain,
              "damping": total * gain, "target_position": position * coordinate,
              "target_velocity": velocity * coordinate}
    for creator, field in (("CreateTypeAttr", "type"), ("CreateStiffnessAttr", "stiffness"),
            ("CreateDampingAttr", "damping"), ("CreateTargetPositionAttr", "target_position"),
            ("CreateTargetVelocityAttr", "target_velocity")):
        getattr(drive, creator)(values[field])
    if active:
        values["max_force"] = declaration["max_force"]["value"]
        drive.CreateMaxForceAttr(values["max_force"])
    # SI command metadata is dimensionless or token/string, so unit conversion
    # must not rescale it. Linear commands use the stage's metersPerUnit.
    for field, typ, value in (("driveMode", Sdf.ValueTypeNames.Token, mode),
            ("driveVelocityWeight", Sdf.ValueTypeNames.Double, velocity_weight),
            ("driveCoordinateUnit", Sdf.ValueTypeNames.Token, "rad" if angular else "m")):
        prim.CreateAttribute("simplecad:" + field, typ, custom=True).Set(value)
    return {"name": name, **values, "mode": mode, "declaration": declaration,
            "passive_damping_si": passive, "active_damping_si": damping,
            "target_velocity_weight": velocity_weight,
            "equation": "F=K*(q_target-q)+Da*(v_target-v)-Dp*v; capped as one combined drive",
            "combination": "D=Dp+Da; effective target velocity=Da/(Dp+Da)*target velocity; K unchanged",
            "cap_scope": "combined active and passive force" if active else "unbounded passive damping (legacy)",
            "conversion": "angular K,D multiply pi/180; angular targets multiply 180/pi; max_force remains SI torque/force before stage conversion"}


def author_joint_extensions(prim, kind, settings, initial_coordinate, articulation_enabled):
    """Portable exact schema spelling, verified against local 107.3 resources."""
    from pxr import Sdf
    fields = {}
    angular = kind == "revolute"
    for key, attribute in (("max_velocity", "physxJoint:maxJointVelocity"),
                            ("friction", "physxJoint:jointFriction")):
        if key not in settings:
            continue
        if not articulation_enabled:
            raise ValueError(f"{key} requires assembly.articulation.enabled=true in PhysX 107.3")
        prim.AddAppliedSchema("PhysxJointAPI")
        value = settings[key]["value"] * (180 / math.pi if angular and key == "max_velocity" else 1)
        prim.CreateAttribute(attribute, Sdf.ValueTypeNames.Float, custom=False).Set(value)
        fields[key] = {"attribute": attribute, "value": value, "parameter": settings[key]}
    if articulation_enabled:
        name = "angular" if angular else "linear"
        prim.AddAppliedSchema("PhysicsJointStateAPI:" + name)
        position = initial_coordinate * (180 / math.pi if angular else 1)
        prefix = "state:" + name + ":physics:"
        prim.CreateAttribute(prefix + "position", Sdf.ValueTypeNames.Float, custom=False).Set(position)
        prim.CreateAttribute(prefix + "velocity", Sdf.ValueTypeNames.Float, custom=False).Set(0.0)
        fields["initial_state"] = {"schema": "PhysicsJointStateAPI:" + name,
            "position_attribute": prefix + "position", "position": position,
            "velocity_attribute": prefix + "velocity", "velocity": 0.0,
            "source": "relative solved connector frames; CAD native initial coordinate retained in joint report",
            "unit": "degree" if angular else "m"}
    return fields


def update_drive_targets(stage, joint_id, *, target_position=None, target_velocity=None):
    """Send SI targets to an existing exported ordinary joint's force drive.

    `target_velocity` is the active target before passive damping combination.
    Position uses rad or m; velocity uses rad/s or m/s. Never writes joint state.
    """
    from pxr import UsdGeom, UsdPhysics
    matches = [p for p in stage.Traverse() if p.GetAttribute("simplecad:jointId").Get() == joint_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one exported joint for stable ID {joint_id!r}; found {len(matches)}")
    prim = matches[0]
    mode = prim.GetAttribute("simplecad:driveMode").Get()
    if mode not in {"position", "velocity"}:
        raise ValueError("Joint has no active exported drive")
    angular = prim.IsA(UsdPhysics.RevoluteJoint)
    name = "angular" if angular else "linear"
    if not prim.HasAPI(UsdPhysics.DriveAPI, name):
        raise ValueError("Exported DriveAPI is missing; runtime may not create it")
    drive = UsdPhysics.DriveAPI(prim, name)
    factor = 180 / math.pi if angular else 1 / UsdGeom.GetStageMetersPerUnit(stage)
    weight = prim.GetAttribute("simplecad:driveVelocityWeight").Get()
    if weight is None:
        raise ValueError("Exported SI target conversion metadata is missing")
    values = {}
    for field, value in (("position", target_position), ("velocity", target_velocity)):
        if value is None:
            continue
        if isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError("Runtime targets must be finite SI numbers")
        if field == "position" and mode != "position":
            raise ValueError("Cannot send a position command to velocity mode")
        attr = drive.GetTargetPositionAttr() if field == "position" else drive.GetTargetVelocityAttr()
        if not attr.HasAuthoredValueOpinion():
            raise ValueError("Exported target attribute is missing")
        converted = float(value) * factor * (weight if field == "velocity" else 1)
        if field == "position":
            lower, upper = prim.GetAttribute("physics:lowerLimit").Get(), prim.GetAttribute("physics:upperLimit").Get()
            if not lower <= converted <= upper:
                raise ValueError("Runtime target_position lies outside the saved physical limits")
        values[field] = (attr, converted)
    for attr, value in values.values():
        attr.Set(value)
    return {"joint_id": joint_id, "prim_path": str(prim.GetPath()), "drive": name,
            "authored_targets": {field: value for field, (_, value) in values.items()}}


def author_mimic_couplings(stage, settings, joints, articulation):
    """One pinion angular coordinate constrains one rack linear coordinate."""
    from pxr import Sdf, UsdPhysics
    by_id = {j["joint_id"]: j for j in joints if j["authored"]}
    result = []
    for identity, declaration in settings.get("couplings", {}).items():
        if not articulation.get("enabled"):
            raise ValueError("Mimic coupling requires an exported articulation")
        reference = by_id.get(declaration["reference_joint_id"])
        follower = by_id.get(declaration["follower_joint_id"])
        if not reference or not follower:
            raise ValueError("Mimic joints must both be authored in the connected ordinary-joint articulation")
        if reference["prim_path"] not in articulation["joint_paths"] or follower["prim_path"] not in articulation["joint_paths"]:
            raise ValueError("Mimic joints must belong to the same articulation")
        gearing = declaration["gearing"]["value"]
        offset = declaration["offset"]["value"]
        residual = follower["initial_coordinate_si"] + gearing * reference["initial_coordinate_si"] + offset
        if abs(residual) > 1e-7:
            raise ValueError(f"Mimic initial CAD coordinates violate qf+g*qr+offset=0: {identity} residual {residual} m")
        prim = stage.GetPrimAtPath(follower["prim_path"])
        if prim.HasAPI(UsdPhysics.DriveAPI, "linear"):
            raise ValueError("Mimic follower must not carry an independent DriveAPI")
        # Single-DOF axes ignore these tags in local 107.3.18; use the exact
        # rotX/rotZ layout proven by the native minimal mixed-joint fixture.
        prim.AddAppliedSchema("PhysxMimicJointAPI:rotX")
        prefix = "physxMimicJoint:rotX:"
        relationship = prefix + "referenceJoint"
        prim.CreateRelationship(relationship, custom=False).SetTargets([Sdf.Path(reference["prim_path"])])
        fields = {prefix + "referenceJointAxis": (Sdf.ValueTypeNames.Token, "rotZ"),
                  prefix + "gearing": (Sdf.ValueTypeNames.Float, gearing * math.pi / 180),
                  prefix + "offset": (Sdf.ValueTypeNames.Float, offset)}
        for name, (typ, value) in fields.items():
            prim.CreateAttribute(name, typ, custom=False,
                variability=Sdf.VariabilityUniform if typ == Sdf.ValueTypeNames.Token else Sdf.VariabilityVarying).Set(value)
        result.append({"coupling_id": identity, "declaration": declaration,
            "follower_path": follower["prim_path"], "reference_path": reference["prim_path"],
            "api_schema": "PhysxMimicJointAPI:rotX", "relationship": relationship,
            "fields": {name: value for name, (_, value) in fields.items()},
            "initial_residual_m": residual, "equation_si": "q_follower+gearing*q_reference+offset=0",
            "approximation": "ideal coordinate constraint; no tooth contact, backlash, compliance or gear losses",
            "follower_drive_authored": False})
    return result
