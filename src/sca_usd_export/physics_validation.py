"""Compare reopened USD physics against the resolved package configuration.

Checks values and relationships as well as counts, including joint endpoint
frames and axes. No simulation success is implied by these static checks.
"""

import math

import numpy as np


def validate_physics(stage, expected, *, tolerance):
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics, UsdShade
    from .mechanical_properties import validate_optional

    def same(actual, desired, field):
        if isinstance(desired, (int, float)) and not isinstance(desired, bool):
            valid = actual is not None and math.isclose(actual, desired, rel_tol=2e-6, abs_tol=1e-12)
        else:
            valid = actual == desired
        if not valid:
            raise ValueError(f"USD physics {field} changed: {actual!r} != {desired!r}")

    scene_config = expected["scene"]
    scene = UsdPhysics.Scene.Get(stage, scene_config["prim_path"])
    if not scene:
        raise ValueError("USD physics scene is missing")
    if not np.allclose(scene.GetGravityDirectionAttr().Get(), scene_config["gravity_direction"], rtol=0, atol=1e-7):
        raise ValueError("USD gravity direction differs from configured up axis")
    same(scene.GetGravityMagnitudeAttr().Get(), scene_config["gravity_m_s2"], "gravity magnitude")
    cache = UsdGeom.XformCache()
    part_count = 0
    for group in expected["groups"]:
        body = stage.GetPrimAtPath(group["body_path"])
        mode = group.get("mode", "dynamic" if group["dynamic"] else "static")
        if mode == "static":
            if body.HasAPI(UsdPhysics.RigidBodyAPI):
                raise ValueError("USD static group gained a rigid body API")
        else:
            if not body.HasAPI(UsdPhysics.RigidBodyAPI):
                raise ValueError("USD rigid body API missing")
            rigid = UsdPhysics.RigidBodyAPI(body)
            same(rigid.GetRigidBodyEnabledAttr().Get(), True, "rigid body enabled")
            same(rigid.GetKinematicEnabledAttr().Get(), mode == "kinematic", "kinematic enabled")
        for part in group["parts"]:
            shape = stage.GetPrimAtPath(part["shape_path"])
            if not shape or not shape.HasAPI(UsdPhysics.CollisionAPI):
                raise ValueError(f"USD collider API missing: {part['node_id']}")
            if not shape.HasAPI(UsdPhysics.MeshCollisionAPI):
                raise ValueError(f"USD mesh collision API missing: {part['node_id']}")
            same(UsdPhysics.CollisionAPI(shape).GetCollisionEnabledAttr().Get(), part["collision_enabled"], "collision enabled " + part["node_id"])
            same(UsdPhysics.MeshCollisionAPI(shape).GetApproximationAttr().Get(), part["collision_approximation"], "collision approximation " + part["node_id"])
            targets = shape.GetRelationship("material:binding:physics").GetTargets()
            if targets != [Sdf.Path(part["contact_material_path"])]:
                raise ValueError("USD contact material binding changed: " + part["node_id"])
            bound, relationship = UsdShade.MaterialBindingAPI(shape).ComputeBoundMaterial(materialPurpose="physics")
            if not bound or str(bound.GetPath()) != part["contact_material_path"]:
                raise ValueError("USD composed physics material differs from expected binding")
            material = UsdPhysics.MaterialAPI(bound.GetPrim())
            if not bound.GetPrim().HasAPI(UsdPhysics.MaterialAPI):
                raise ValueError("USD contact material Physics API is missing")
            for field, getter in (("static_friction", "GetStaticFrictionAttr"),
                                  ("dynamic_friction", "GetDynamicFrictionAttr"),
                                  ("restitution", "GetRestitutionAttr")):
                same(getattr(material, getter)().Get(), part["contact"][field]["value"], field + " " + part["node_id"])
            same(UsdPhysics.MassAPI(shape).GetDensityAttr().Get(), part["density_kg_m3"], "native density " + part["node_id"])
            part_count += 1

    expected_filters = {}
    for pair in expected.get("collision_exclusions", []):
        expected_filters.setdefault(pair["a_path"], set()).add(pair["b_path"])
    required_pairs = {frozenset((pair["a_path"], pair["b_path"]))
                      for pair in expected.get("collision_exclusions", [])}
    groups_by_id = {group["group_id"]: group for group in expected["groups"]}
    for joint in expected["joints"]:
        collision = joint.get("collision_enabled", joint.get("requested_configuration", {}).get("collision_enabled"))
        if collision and collision["value"] is False:
            for first in groups_by_id[joint["body0_group"]]["parts"]:
                for second in groups_by_id[joint["body1_group"]]["parts"]:
                    if frozenset((first["shape_path"], second["shape_path"])) not in required_pairs:
                        raise ValueError("USD joint collision policy has no explicit collider pair filter: " + joint["joint_id"])
    for group in expected["groups"]:
        for part in group["parts"]:
            prim = stage.GetPrimAtPath(part["shape_path"])
            actual = {str(p) for p in prim.GetRelationship("physics:filteredPairs").GetTargets()}
            if actual != expected_filters.get(part["shape_path"], set()):
                raise ValueError("USD collision exclusion changed: " + part["node_id"])
            if actual and not prim.HasAPI(UsdPhysics.FilteredPairsAPI):
                raise ValueError("USD collision exclusion API is missing: " + part["node_id"])

    joint_count = 0
    for entry in expected["joints"]:
        if not entry["authored"]:
            continue
        prim = stage.GetPrimAtPath(entry["prim_path"])
        cls = UsdPhysics.RevoluteJoint if entry["joint_type"] == "revolute" else UsdPhysics.PrismaticJoint
        joint = cls(prim)
        if not joint:
            raise ValueError("USD joint type changed: " + entry["joint_id"])
        same(joint.GetAxisAttr().Get(), "Z", "joint axis " + entry["joint_id"])
        same(joint.GetCollisionEnabledAttr().Get(), entry["collision_enabled"]["value"], "joint endpoint collision " + entry["joint_id"])
        for suffix in ("0", "1"):
            targets = getattr(joint, "GetBody" + suffix + "Rel")().GetTargets()
            if targets != [Sdf.Path(entry["body" + suffix + "_path"])]:
                raise ValueError("USD joint body relationship changed: " + entry["joint_id"])
            frame = entry["frames"][suffix]
            position = getattr(joint, "GetLocalPos" + suffix + "Attr")().Get()
            rotation = getattr(joint, "GetLocalRot" + suffix + "Attr")().Get()
            if not np.allclose(position, frame["position_m"], rtol=0, atol=tolerance):
                raise ValueError("USD joint anchor changed: " + entry["joint_id"])
            actual_rotation = np.array(Gf.Matrix3d(Gf.Rotation(Gf.Quatd(rotation)))).T
            if not np.allclose(actual_rotation, frame["rotation_matrix"], rtol=0, atol=2e-6):
                raise ValueError("USD joint local orientation changed: " + entry["joint_id"])
            body_world = np.array(cache.GetLocalToWorldTransform(stage.GetPrimAtPath(targets[0]))).T
            world_anchor = body_world[:3, :3] @ np.array(position) + body_world[:3, 3]
            world_axis = body_world[:3, :3] @ actual_rotation[:, 2]
            if not np.allclose(world_anchor, frame["world_position_m"], atol=tolerance, rtol=0):
                raise ValueError("USD joint world anchor changed: " + entry["joint_id"])
            if not np.allclose(world_axis, frame["world_axis"], atol=2e-6, rtol=0):
                raise ValueError("USD joint world axis changed: " + entry["joint_id"])
        if not entry.get("drive_authored", True):
            if UsdPhysics.DriveAPI.GetAll(prim):
                raise ValueError("USD mimic follower acquired an independent DriveAPI")
        else:
            drive_config = entry.get("effective_drive", entry["passive_drive"])
            drive = UsdPhysics.DriveAPI(prim, drive_config["name"])
            if not prim.HasAPI(UsdPhysics.DriveAPI, drive_config["name"]):
                raise ValueError("USD passive joint damping drive is missing")
            for getter, key in (("GetTypeAttr", "type"), ("GetStiffnessAttr", "stiffness"),
                                ("GetDampingAttr", "damping"), ("GetTargetPositionAttr", "target_position"),
                                ("GetTargetVelocityAttr", "target_velocity")):
                same(getattr(drive, getter)().Get(), drive_config[key], "combined drive " + key)
            if "max_force" in drive_config:
                same(drive.GetMaxForceAttr().Get(), drive_config["max_force"], "combined drive max force")
                if not drive.GetMaxForceAttr().HasAuthoredValueOpinion():
                    raise ValueError("USD active drive maxForce is not authored")
            elif drive.GetMaxForceAttr().HasAuthoredValueOpinion():
                raise ValueError("USD passive drive acquired an undeclared force cap")
            if "effective_drive" in entry:
                same(prim.GetAttribute("simplecad:driveMode").Get(), drive_config["mode"], "drive mode")
                same(prim.GetAttribute("simplecad:driveVelocityWeight").Get(), drive_config["target_velocity_weight"], "drive velocity weight")
        for field, extension in entry.get("physx_extensions", {}).items():
            if field == "initial_state":
                if extension["schema"] not in prim.GetAppliedSchemas():
                    # usd-core lacks PhysX plugins, so GetAppliedSchemas may
                    # omit unknown-but-correct authored schema tokens.
                    if extension["schema"] not in prim.GetMetadata("apiSchemas").GetAddedOrExplicitItems():
                        raise ValueError("USD articulation joint initial-state API missing")
                for key in ("position", "velocity"):
                    attr = prim.GetAttribute(extension[key + "_attribute"])
                    same(attr.Get(), extension[key], "initial joint state " + key)
                    if str(attr.GetTypeName()) != "float" or not attr.HasAuthoredValueOpinion():
                        raise ValueError("USD joint state missing/type changed")
            else:
                attr = prim.GetAttribute(extension["attribute"])
                same(attr.Get(), extension["value"], "joint " + field)
                if str(attr.GetTypeName()) != "float" or not attr.HasAuthoredValueOpinion():
                    raise ValueError("USD joint extension missing/type changed")
                if "PhysxJointAPI" not in prim.GetMetadata("apiSchemas").GetAddedOrExplicitItems():
                    raise ValueError("USD PhysxJointAPI missing")
        limits = entry["limits"]
        for getter, value in (("GetLowerLimitAttr", limits["lower"] if limits else -math.inf),
                              ("GetUpperLimitAttr", limits["upper"] if limits else math.inf)):
            same(getattr(joint, getter)().Get(), value, "joint limit")
        joint_count += 1
    articulation = expected.get("articulation", {})
    if articulation.get("enabled"):
        root = stage.GetPrimAtPath(articulation["root_prim_path"])
        if not root or not root.HasAPI(UsdPhysics.ArticulationRootAPI):
            raise ValueError("USD articulation root API missing")
        if "self_collisions" in articulation:
            if "PhysxArticulationAPI" not in root.GetMetadata("apiSchemas").GetAddedOrExplicitItems():
                raise ValueError("USD PhysxArticulationAPI missing")
            attr = root.GetAttribute("physxArticulation:enabledSelfCollisions")
            same(attr.Get(), articulation["self_collisions"]["value"], "articulation self collisions")
            if str(attr.GetTypeName()) != "bool" or not attr.HasAuthoredValueOpinion():
                raise ValueError("USD articulation self-collision policy missing/type changed")
        fixed = articulation.get("synthetic_world_fixed")
        if fixed:
            joint = UsdPhysics.FixedJoint.Get(stage, fixed["prim_path"])
            if not joint or joint.GetBody0Rel().GetTargets() or joint.GetBody1Rel().GetTargets() != [Sdf.Path(fixed["body1_path"])]:
                raise ValueError("USD articulation world fixed joint changed")
            if "world_position_m" in fixed:
                if not np.allclose(joint.GetLocalPos0Attr().Get(), fixed["world_position_m"], atol=tolerance, rtol=0):
                    raise ValueError("USD articulation world fixed anchor changed")
                expected_rotation = fixed["world_rotation_wxyz"]
                actual_rotation = joint.GetLocalRot0Attr().Get()
                if not np.allclose([actual_rotation.GetReal(), *actual_rotation.GetImaginary()], expected_rotation, atol=2e-6, rtol=0):
                    raise ValueError("USD articulation world fixed rotation changed")
                if not np.allclose(joint.GetLocalPos1Attr().Get(), [0, 0, 0], atol=tolerance, rtol=0):
                    raise ValueError("USD articulation root local fixed anchor changed")
        for path in articulation["dynamic_body_paths"]:
            if not stage.GetPrimAtPath(path).HasAPI(UsdPhysics.RigidBodyAPI):
                raise ValueError("USD articulation link RigidBodyAPI missing")
    for coupling in expected.get("couplings", []):
        prim = stage.GetPrimAtPath(coupling["follower_path"])
        if coupling["api_schema"] not in prim.GetMetadata("apiSchemas").GetAddedOrExplicitItems():
            raise ValueError("USD mimic API missing")
        if prim.GetRelationship(coupling["relationship"]).GetTargets() != [Sdf.Path(coupling["reference_path"])]:
            raise ValueError("USD mimic reference joint changed")
        for name, value in coupling["fields"].items():
            attr = prim.GetAttribute(name)
            same(attr.Get(), value, "mimic " + name)
            if not attr.HasAuthoredValueOpinion() or str(attr.GetTypeName()) != ("token" if isinstance(value, str) else "float"):
                raise ValueError("USD mimic attribute missing/type changed")
    return {**validate_optional(stage, expected.get("optional_properties", [])),
            "contact_parts_checked": part_count, "passive_joints_checked": joint_count,
            "collision_exclusions_checked": len(expected.get("collision_exclusions", [])),
            "joint_bodies_anchors_axes_checked": True,
            "articulation_topology_checked": bool(articulation.get("enabled")),
            "mimic_couplings_checked": len(expected.get("couplings", [])),
            "active_drives_checked": sum((j.get("effective_drive") or {}).get("mode", "off") != "off" for j in expected["joints"])}
