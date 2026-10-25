"""Independent reopened rigid-chain checks. Reports remain SI, never simulated."""
import json
import math

import numpy as np

from .rigid_joint_chain import material_filter_indices, plan_chain, preflight_resources, scene_settings, value


def validate_rigid_chains(stage, chains, physics_report, *, tolerance=1e-8):
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics
    from .mechanical_properties import applied_schema_tokens

    cache = UsdGeom.XformCache()
    groups = {member: group for group in physics_report["groups"] for member in group["members"]}

    def equal(actual, expected, description, *, coordinate=False):
        if isinstance(expected, (str, bool)) or expected is None:
            ok = actual == expected
        elif isinstance(expected, list) and (not expected or isinstance(expected[0], str)):
            ok = list(actual) == expected
        else:
            try:
                a, b = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
                ok = a.shape == b.shape and np.allclose(a, b, rtol=0 if coordinate else 2e-6,
                                                       atol=tolerance if coordinate else 1e-12)
            except (TypeError, ValueError):
                ok = False
        if not ok:
            raise ValueError(f"Rigid chain {description} changed: {actual!r} != {expected!r}")

    def quaternion(item):
        return np.array([item.GetReal(), *item.GetImaginary()], dtype=float)

    def equal_quat(actual, expected, description):
        a, b = quaternion(actual), np.asarray(expected, dtype=float)
        if not (np.allclose(a, b, rtol=0, atol=2e-6) or np.allclose(a, -b, rtol=0, atol=2e-6)):
            raise ValueError("Rigid chain quaternion changed: " + description)

    def world_point(body_path, point):
        if not body_path:
            return np.asarray(point, dtype=float)
        xf = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(body_path))
        return np.asarray(xf.Transform(Gf.Vec3d(*map(float, point))))

    if not chains:
        return {"chain_count": 0, "simulation_verified": False}
    declarations = {entry["chain_id"]: entry["declaration"] for entry in chains}
    resources = preflight_resources(declarations, physics_report, authored=True)
    if resources is not None:
        if physics_report["scene"].get("rigid_chain_resources") != resources:
            raise ValueError("Rigid chain scene resource usage changed")
    policy = scene_settings(declarations)
    scene = stage.GetPrimAtPath(physics_report["scene"]["prim_path"])
    for suffix, expected in (("enableGPUDynamics", policy["enable_gpu_dynamics"]),
                             ("broadphaseType", policy["broadphase_type"]),
                             ("solverType", policy["solver_type"]), ("timeStepsPerSecond", policy["physics_hz"])):
        equal(scene.GetAttribute("physxScene:" + suffix).Get(), expected, "scene." + suffix)
    if "rigid_chain_runtime_policy" in physics_report["scene"]:
        runtime_policy = physics_report["scene"]["rigid_chain_runtime_policy"]
        if (not isinstance(runtime_policy, dict) or set(runtime_policy) != {"external_forces_every_iteration"}
                or runtime_policy["external_forces_every_iteration"] is not True):
            raise ValueError("Rigid chain scene runtime policy must require external forces every iteration")
        prop = scene.GetAttribute("physxScene:enableExternalForcesEveryIteration")
        if not prop or not prop.HasAuthoredValueOpinion() or str(prop.GetTypeName()) != "bool" or prop.IsCustom():
            raise ValueError("Rigid chain scene.enableExternalForcesEveryIteration type/authored state changed")
        equal(prop.Get(), True, "scene.enableExternalForcesEveryIteration")
    equal(stage.GetTimeCodesPerSecond(), policy["physics_hz"], "timeCodesPerSecond")
    links_checked, joints_checked, pairs_checked = 0, 0, 0
    for entry in chains:
        declaration = entry["declaration"]
        if ("resolution" in declaration) != ("resolved_configuration" in entry) or (
                "resolution" in declaration and entry["resolved_configuration"] != declaration["resolution"]):
            raise ValueError("Rigid chain resolved configuration changed")
        if resources is not None and entry.get("resource_usage") != resources:
            raise ValueError("Rigid chain resource usage changed")
        plan = plan_chain(declaration)
        root = stage.GetPrimAtPath(entry["prim_path"])
        if not root or root.GetAttribute("sca:chain_id").Get() != entry["chain_id"]:
            raise ValueError("Rigid chain identity is missing/changed")
        equal(root.GetAttribute("sca:representation").Get(), "rigid_joint_chain", "representation")
        if json.loads(root.GetAttribute("sca:physicsDeclaration").Get()) != declaration:
            raise ValueError("Rigid chain persisted declaration changed")
        equal(entry["stations_m"], plan["stations_m"], "reported material stations", coordinate=True)
        equal(entry["points_world_m"], plan["points_world_m"], "reported initial points", coordinate=True)
        equal(entry["total_mass_kg"], sum(plan["masses_kg"]), "reported mass sum")
        links = entry["links"]
        if len(links) != len(plan["arc_lengths_m"]):
            raise ValueError("Rigid chain link count differs from declared discretization")
        semantic = stage.GetPrimAtPath(entry["target_occurrence_path"])
        if UsdGeom.Imageable(semantic).ComputeVisibility() != "invisible":
            raise ValueError("Rigid chain source CAD must remain hidden")
        for relation in ("sca:physicsBodies", "sca:physicsGeometry", "sca:renderGeometry"):
            equal([str(x) for x in semantic.GetRelationship(relation).GetTargets()], [link["path"] for link in links], relation)
        equal([str(x) for x in semantic.GetRelationship("sca:rigidJointChain").GetTargets()], [entry["prim_path"]], "CAD backlink")
        material = UsdPhysics.MaterialAPI(stage.GetPrimAtPath(entry["material_path"]))
        for field, getter in (("static_friction", "GetStaticFrictionAttr"), ("dynamic_friction", "GetDynamicFrictionAttr"), ("restitution", "GetRestitutionAttr")):
            equal(getattr(material, getter)().Get(), value(declaration["contact"][field]), "material." + field)
        world_bounds = []
        for index, link in enumerate(links):
            prim = stage.GetPrimAtPath(link["path"])
            if not prim or not prim.IsA(UsdGeom.Capsule):
                raise ValueError("Rigid chain Capsule is missing: " + link["path"])
            capsule = UsdGeom.Capsule(prim)
            equal(capsule.GetAxisAttr().Get(), "X", "capsule axis")
            equal(capsule.GetHeightAttr().Get(), plan["chord_lengths_m"][index], "capsule chord height")
            equal(capsule.GetRadiusAttr().Get(), plan["radii_m"][index], "capsule radius")
            p0, p1 = np.asarray(plan["points_world_m"])[index:index+2]
            matrix = cache.GetLocalToWorldTransform(prim)
            center = np.asarray(matrix.Transform(Gf.Vec3d(0)))
            equal(center, (p0+p1)/2, "capsule world center", coordinate=True)
            tangent = np.asarray(matrix.TransformDir(Gf.Vec3d(1, 0, 0)))
            planned_tangent = (p1-p0)/plan["chord_lengths_m"][index]
            rotation = Gf.Rotation(Gf.Vec3d(1, 0, 0), Gf.Vec3d(*map(float, planned_tangent))).GetQuat()
            expected_quat = Gf.Quatf(float(rotation.GetReal()), Gf.Vec3f(*map(float, rotation.GetImaginary())))
            authored_quat = prim.GetAttribute("xformOp:orient").Get()
            if not np.array_equal(quaternion(authored_quat), quaternion(expected_quat)):
                raise ValueError("Rigid chain authored capsule orientation changed")
            equal(quaternion(authored_quat), link["orientation_wxyz"], "reported capsule orientation")
            # XformOp orient uses Gf.Rotation (axis/angle) from its quatf,
            # not Matrix4d.SetRotate(quatd) directly. Near zero angle the
            # rounded real component can change its represented angle by
            # several microradians. Reproduce USD's actual sequence exactly;
            # do not widen geometry or dynamic acceptance tolerances.
            expected_rotation = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Quatd(expected_quat)))
            equal(tangent, np.asarray(expected_rotation.TransformDir(Gf.Vec3d(1, 0, 0))),
                  "capsule represented axis", coordinate=True)
            equal_quat(matrix.ExtractRotationQuat(), quaternion(expected_rotation.ExtractRotationQuat()), "capsule world orientation")
            half = plan["chord_lengths_m"][index]/2
            radius = plan["radii_m"][index]
            endpoints = np.vstack([center-half*tangent, center+half*tangent])
            world_bounds.append([endpoints.min(axis=0)-radius, endpoints.max(axis=0)+radius])
            if not prim.HasAPI(UsdPhysics.RigidBodyAPI) or not prim.HasAPI(UsdPhysics.CollisionAPI):
                raise ValueError("Rigid chain body/collider API missing")
            body = UsdPhysics.RigidBodyAPI(prim)
            equal(body.GetRigidBodyEnabledAttr().Get(), True, "body enabled")
            equal(body.GetKinematicEnabledAttr().Get(), False, "body dynamic")
            equal(body.GetVelocityAttr().Get(), [0, 0, 0], "initial velocity")
            equal(body.GetAngularVelocityAttr().Get(), [0, 0, 0], "initial angular velocity")
            equal(UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get(), value(declaration["collision"]["enabled"]), "collision enabled")
            mass = UsdPhysics.MassAPI(prim)
            equal(mass.GetMassAttr().Get(), plan["masses_kg"][index], "arc-length mass")
            equal(mass.GetCenterOfMassAttr().Get(), [0, 0, 0], "center of mass")
            equal(mass.GetDiagonalInertiaAttr().Get(), plan["inertia_kg_m2"][index], "capsule inertia")
            equal_quat(mass.GetPrincipalAxesAttr().Get(), [1, 0, 0, 0], "capsule principal axes")
            equal([str(x) for x in prim.GetRelationship("material:binding:physics").GetTargets()], [entry["material_path"]], "contact material binding")
            links_checked += 1
        endpoint_map = {item["endpoint"]: item for item in entry["endpoints"]}
        expected_pairs = set()
        expected_pairs.update((links[first]["path"], links[second]["path"])
                              for first, second in material_filter_indices(plan, declaration))
        for end, index in (("start", 0), ("end", len(links)-1)):
            declared = declaration["endpoints"][end]
            reported = endpoint_map[end]
            equal(reported["mode"], declared["mode"], "endpoint mode")
            if declared["mode"] == "body_fixed":
                group = groups[declared["rigid_node_id"]]
                actor = group["body_path"] if group["mode"] != "static" else None
                equal(reported["rigid_body_path"], actor, "endpoint actor")
                colliders = [part["shape_path"] for part in group["parts"]]
                equal(reported["collider_paths"], colliders, "endpoint leaf colliders")
                if value(declaration["collision"]["filter_endpoint_links"]):
                    expected_pairs.update((links[index]["path"], shape) for shape in colliders)
            if declared["mode"] == "free" and reported["joint_path"] is not None:
                raise ValueError("Free rigid chain endpoint gained a constraint")
        if {tuple(pair) for pair in entry["filtered_pairs"]} != expected_pairs:
            raise ValueError("Rigid chain report filter policy does not match its declaration")
        pairs_by_source = {}
        for first, second in expected_pairs:
            pairs_by_source.setdefault(first, set()).add(second)
        for link in links:
            actual = {str(x) for x in stage.GetPrimAtPath(link["path"]).GetRelationship("physics:filteredPairs").GetTargets()}
            desired = pairs_by_source.get(link["path"], set())
            if actual != desired:
                raise ValueError("Rigid chain leaf/adjacent collision filters changed: " + link["path"])
            if desired and not stage.GetPrimAtPath(link["path"]).HasAPI(UsdPhysics.FilteredPairsAPI):
                raise ValueError("Rigid chain filteredPairs API missing")
        pairs_checked += len(expected_pairs)
        desired_joint_count = len(links)-1 + sum(item["mode"] != "free" for item in declaration["endpoints"].values())
        if len(entry["joints"]) != desired_joint_count:
            raise ValueError("Rigid chain joint count differs from endpoints/discretization")
        for index, record in enumerate(entry["joints"]):
            joint = UsdPhysics.Joint(stage.GetPrimAtPath(record["path"]))
            if not joint or joint.GetPrim().GetTypeName() != record["type"]:
                raise ValueError("Rigid chain joint missing/type changed")
            equal(joint.GetCollisionEnabledAttr().Get(), False, "joint collision flag")
            equal(joint.GetExcludeFromArticulationAttr().Get(), True, "excludeFromArticulation")
            internal = record["kind"] == "internal_d6"
            anchor = np.asarray(plan["points_world_m"])[index+1] if internal else np.asarray(plan["points_world_m"])[0 if record["endpoint"] == "start" else -1]
            for suffix in ("0", "1"):
                targets = [str(x) for x in getattr(joint, "GetBody"+suffix+"Rel")().GetTargets()]
                equal(targets, record["body"+suffix], "joint body"+suffix)
                pos = getattr(joint, "GetLocalPos"+suffix+"Attr")().Get()
                equal(pos, record["local_position_"+suffix+"_m"], "local anchor", coordinate=True)
                equal(world_point(targets[0] if targets else None, pos), anchor, "world joint anchor", coordinate=True)
                rotation = getattr(joint, "GetLocalRot"+suffix+"Attr")().Get()
                equal_quat(rotation, [1, 0, 0, 0] if internal else record["local_rotation_"+suffix+"_wxyz"], "natural joint frame")
            if internal:
                if record["body0"] != [links[index]["path"]] or record["body1"] != [links[index+1]["path"]]:
                    raise ValueError("Rigid chain internal adjacency changed")
                ell = (plan["arc_lengths_m"][index]+plan["arc_lengths_m"][index+1])/2
                bend = min(value(declaration["limits"]["maximum_bend_angle"]), math.degrees(ell/value(declaration["limits"]["minimum_bend_radius"])))
                for axis in ("transX", "transY", "transZ"):
                    limit = UsdPhysics.LimitAPI.Get(joint.GetPrim(), axis)
                    equal(limit.GetLowAttr().Get(), 1, "D6 translation lock low")
                    equal(limit.GetHighAttr().Get(), -1, "D6 translation lock high")
                for axis in ("rotX", "rotY", "rotZ"):
                    angle = value(declaration["limits"]["maximum_twist_angle"]) if axis == "rotX" else bend
                    limit = UsdPhysics.LimitAPI.Get(joint.GetPrim(), axis)
                    equal(limit.GetLowAttr().Get(), -angle, "D6 angular lower limit")
                    equal(limit.GetHighAttr().Get(), angle, "D6 angular upper limit")
                    drive = UsdPhysics.DriveAPI.Get(joint.GetPrim(), axis)
                    rigidity = value(declaration["mechanics"]["torsional_rigidity" if axis == "rotX" else "bending_rigidity"])
                    equal(drive.GetTypeAttr().Get(), "force", "angular drive type")
                    equal(drive.GetStiffnessAttr().Get(), rigidity/ell*math.pi/180, "angular stiffness EI/GJ over ell")
                    equal(drive.GetDampingAttr().Get(), value(declaration["mechanics"]["joint_damping"])*math.pi/180, "per-joint damping")
                    equal(drive.GetTargetPositionAttr().Get(), 0, "straight natural angular position")
                    equal(drive.GetTargetVelocityAttr().Get(), 0, "angular target velocity")
                    equal(drive.GetMaxForceAttr().Get(), math.inf, "unbounded drive torque")
            joints_checked += 1
        for record in entry["authored_properties"]:
            prim = stage.GetPrimAtPath(record["prim_path"])
            prop = prim.GetAttribute(record["attribute"])
            if not prop or not prop.HasAuthoredValueOpinion() or str(prop.GetTypeName()) != record["type"] or prop.IsCustom():
                raise ValueError("Rigid chain native property type/authored state changed")
            equal(prop.Get(), record["value"], record["attribute"])
        actual_bounds = [np.min(np.asarray(world_bounds)[:, 0], axis=0), np.max(np.asarray(world_bounds)[:, 1], axis=0)]
        equal(actual_bounds, entry["bounds_world_m"], "capsule world bounds", coordinate=True)
    return {"chain_count": len(chains), "links_checked": links_checked, "joints_checked": joints_checked,
            "filtered_pairs_checked": pairs_checked, "simulation_verified": False}
