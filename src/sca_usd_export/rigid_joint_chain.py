"""Package-declared rigid capsule/D6 chains. No project or simulator imports.

All calculations and authored values here use SI. stage_units converts the
finished layer, including angular D6 drives, to the requested stage units.
The first contract supports planar initial curves and a straight natural state.
Consequently an initially curved chain has elastic preload. Inputs are estimates
unless their persisted provenance explicitly identifies measurements.
"""
from copy import deepcopy
import hashlib
import json
import math

import numpy as np


REPRESENTATION = "rigid_joint_chain"
BUDGET_FIELDS = ("max_bodies", "max_joints", "max_colliders", "max_filtered_pairs",
                 "max_contact_pairs", "max_solver_work_per_second")


def value(parameter):
    return parameter["value"] if isinstance(parameter, dict) else parameter


def chain_declarations(settings):
    return {key: item for key, item in settings.get("cables", {}).items()
            if item.get("representation") == REPRESENTATION}


def scene_settings(declarations):
    """Require one explicit scene policy; never let the last cable win."""
    policies = []
    for identity, declaration in sorted(declarations.items()):
        policy = {key: value(item) for key, item in declaration["scene"].items()}
        if policies and policy != policies[0][1]:
            raise ValueError(f"Rigid chain scene policy conflict: {policies[0][0]!r} and {identity!r}")
        policies.append((identity, policy))
    return policies[0][1] if policies else None


def capsule_inertia(mass, radius, height):
    """Principal SI inertia of a solid capsule with cylinder length height.

    The mass input is the declared linear density times material ARC length;
    capsule overlap does not add mass. Geometry and mass models are explicit.
    """
    cylinder_volume = math.pi * radius * radius * height
    sphere_volume = 4 * math.pi * radius ** 3 / 3
    mc = mass * cylinder_volume / (cylinder_volume + sphere_volume)
    ms = mass - mc
    axial = mc * radius ** 2 / 2 + ms * 2 * radius ** 2 / 5
    transverse = mc * (3 * radius ** 2 + height ** 2) / 12
    transverse += ms * (2 * radius ** 2 / 5 + height ** 2 / 4 + 3 * height * radius / 8)
    return [axial, transverse, transverse]


def plan_chain(declaration):
    """Reproducible geometry/material plan from the saved declaration alone."""
    geometry = declaration["geometry"]
    original = np.asarray(value(geometry["centerline"]), dtype=float)
    arc = np.asarray(value(geometry["arc_stations"]), dtype=float)
    start, end = value(geometry["free_start"]), value(geometry["free_end"])
    count = value(declaration["discretization"]["free_segments"])
    length = value(geometry["length"])
    stations = np.r_[0.0, np.linspace(start, end, count + 1), length]
    # A zero-length optional relief is absent, rather than a zero-mass body.
    stations = stations[np.r_[True, np.diff(stations) > 1e-12]]
    points = np.column_stack([np.interp(stations, arc, original[:, k]) for k in range(3)])
    arcs = np.diff(stations)
    chords = np.linalg.norm(np.diff(points, axis=0), axis=1)
    if np.any(arcs <= 0) or np.any(chords <= 1e-10) or not np.isfinite(points).all():
        raise ValueError("Rigid chain discretization produces zero/invalid material spans or chords")
    radius = value(declaration["section"]["radius"])
    relief_radius = value(declaration["section"]["endpoint_radius"])
    radii = np.full(len(arcs), radius, dtype=float)
    if start > 1e-12:
        radii[0] = relief_radius
    if end < length - 1e-12:
        radii[-1] = relief_radius
    masses = arcs * value(declaration["mechanics"]["linear_density"])
    inertia = [capsule_inertia(float(m), float(r), float(h))
               for m, r, h in zip(masses, radii, chords)]
    return {"stations_m": stations.tolist(), "points_world_m": points.tolist(),
            "arc_lengths_m": arcs.tolist(), "chord_lengths_m": chords.tolist(),
            "radii_m": radii.tolist(), "masses_kg": masses.tolist(),
            "inertia_kg_m2": inertia}


def material_filter_indices(plan, declaration):
    """Yield exactly the declared local material filters, without an all-pair scan.

    Turning self collision off requires all N(N-1)/2 pairs and is deliberately
    still output-sized work. Callers must check resource budgets before authoring.
    Material-neighbour selection depends on arc coordinates, never world-space
    proximity, so a remote portion folded back onto the cable remains collidable.
    """
    count = len(plan["arc_lengths_m"])
    collision = declaration["collision"]
    if not value(collision["self_collision"]):
        for first in range(count):
            for second in range(first + 1, count):
                yield first, second
        return
    adjacent = value(collision["filter_adjacent"])
    neighbors = value(collision.get("filter_material_neighbors", False))
    if not neighbors:
        if adjacent:
            for first in range(count - 1):
                yield first, first + 1
        return
    stations = np.asarray(plan["stations_m"])
    radii = np.asarray(plan["radii_m"])
    grouped = {}
    for index, radius in enumerate(radii):
        grouped.setdefault(float(radius), []).append(index)
    # The implemented section has at most two radii (free and relief). Search
    # each group independently so one large end cap cannot cause an N^2 scan
    # over all the much smaller interior links.
    radius_groups = [(radius, np.asarray(indices), stations[indices])
                     for radius, indices in grouped.items()]
    for first in range(count - 1):
        if adjacent:
            yield first, first + 1
        for radius, indices, starts in radius_groups:
            low = int(np.searchsorted(indices, first + 1))
            high = int(np.searchsorted(starts, stations[first + 1] + radii[first] + radius + 1e-12, side="right"))
            for second in indices[low:high]:
                if not (adjacent and second == first + 1):
                    yield first, int(second)


def preflight_resources(declarations, physics_report, *, authored=False):
    """Check persisted scene budgets before creating chain prims or pair lists.

    Counts include ordinary rigid groups. Contact pairs are a conservative
    all-collider upper bound, not a prediction of active broadphase contacts.
    Solver work is a deterministic chain-only proxy, not a runtime guarantee.
    Legacy declarations without budgets retain their previous acceptance.
    """
    from sca_physics.cable_resolution import budget_values
    requests = {identity: budget_values(item["resource_budget"]) for identity, item in declarations.items()
                if "resource_budget" in item}
    if not requests:
        return None
    for identity, request in requests.items():
        if (not isinstance(request, dict) or set(request) - set(BUDGET_FIELDS) - {"scope"}
                or set(BUDGET_FIELDS) - set(request) or request.get("scope", "scene") != "scene"
                or any(type(request[key]) is not int or request[key] <= 0 for key in BUDGET_FIELDS)):
            raise ValueError(f"Rigid chain {identity!r} resource_budget must contain positive integer scene limits")
    budget = {key: min(item[key] for item in requests.values()) for key in BUDGET_FIELDS}
    usage = {"bodies": physics_report["body_count"], "joints": physics_report["joint_count"],
             "colliders": physics_report["collider_count"],
             "filtered_pairs": len(physics_report.get("collision_exclusions", [])),
             "solver_work_per_second": 0}
    if authored:
        for entry in physics_report["rigid_chains"]:
            usage["bodies"] -= len(entry["links"])
            usage["colliders"] -= len(entry["links"])
            usage["joints"] -= len(entry["joints"])
    groups = {member: group for group in physics_report["groups"] for member in group["members"]}
    chain_counts = {}

    def require(field):
        allowed = budget["max_" + field]
        if usage[field] > allowed:
            raise ValueError(f"Rigid chain scene resource budget exceeded: {field}={usage[field]} > {allowed}; "
                             "increase the explicit resource_budget or choose a coarser/less costly configuration")

    # This first pass uses scalar counts only. A huge explicit request is rejected
    # before numpy linspace, joint arrays or quadratic collision lists allocate.
    for identity, item in sorted(declarations.items()):
        geometry = item["geometry"]
        count = value(item["discretization"]["free_segments"])
        count += int(value(geometry["free_start"]) > 1e-12)
        count += int(value(geometry["free_end"]) < value(geometry["length"]) - 1e-12)
        joints = count - 1 + sum(end["mode"] != "free" for end in item["endpoints"].values())
        chain_counts[identity] = {"bodies": count, "colliders": count, "joints": joints}
        for field in ("bodies", "colliders", "joints"):
            usage[field] += chain_counts[identity][field]
            require(field)
        usage["solver_work_per_second"] += count * value(item["scene"]["physics_hz"]) * (
            value(item["body"]["solver_position_iterations"]) + value(item["body"]["solver_velocity_iterations"]))
        require("solver_work_per_second")
    for identity, item in sorted(declarations.items()):
        count = chain_counts[identity]["bodies"]
        endpoint_filters = 0
        if value(item["collision"]["filter_endpoint_links"]):
            for end in item["endpoints"].values():
                if end["mode"] == "body_fixed":
                    if end["rigid_node_id"] not in groups:
                        raise ValueError("Rigid chain endpoint does not resolve to a geometric rigid group: " + end["rigid_node_id"])
                    endpoint_filters += len(groups[end["rigid_node_id"]]["parts"])
        if not value(item["collision"]["self_collision"]):
            internal_filters = count * (count - 1) // 2
            usage["filtered_pairs"] += internal_filters + endpoint_filters
            require("filtered_pairs")
        else:
            usage["filtered_pairs"] += endpoint_filters
            require("filtered_pairs")
            internal_filters = 0
            for _ in material_filter_indices(plan_chain(item), item):
                internal_filters += 1
                usage["filtered_pairs"] += 1
                require("filtered_pairs")
        chain_counts[identity]["filtered_pairs"] = internal_filters + endpoint_filters
    usage["contact_pairs"] = max(0, usage["colliders"] * (usage["colliders"] - 1) // 2 - usage["filtered_pairs"])
    require("contact_pairs")
    return {"scope": "scene", "budget": budget, "estimated_usage": usage,
            "chain_counts": chain_counts, "passed": True,
            "contact_pair_estimate": "all collider pairs minus authored filtered pairs; conservative candidate bound, not active contacts",
            "solver_work_estimate": "sum(chain link bodies * physics_hz * (position_iterations + velocity_iterations)); excludes ordinary bodies/contact solve cost",
            "validation_status": "resource budget passed; physical accuracy and runtime performance are not certified"}


def _plain(item):
    if hasattr(item, "GetReal"):
        return [float(item.GetReal()), *map(float, item.GetImaginary())]
    if isinstance(item, np.ndarray):
        return item.tolist()
    if isinstance(item, (tuple, list)):
        return [_plain(x) for x in item]
    if isinstance(item, (float, int, bool, str)) or item is None:
        return "unbounded" if isinstance(item, float) and math.isinf(item) else item
    return list(item)


def author_rigid_joint_chains(stage, declarations, records, physics_report):
    """Author only package-selected chains and explicitly requested end actors."""
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics, UsdShade

    if not declarations:
        return []
    resource_usage = preflight_resources(declarations, physics_report)
    by_node = {record["node_id"]: record for record in records}
    groups = {member: group for group in physics_report["groups"] for member in group["members"]}
    policy = scene_settings(declarations)
    scene = stage.GetPrimAtPath(physics_report["scene"]["prim_path"])
    scene.AddAppliedSchema("PhysxSceneAPI")
    scene_values = {"enableGPUDynamics": ("Bool", policy["enable_gpu_dynamics"]),
                    "broadphaseType": ("Token", policy["broadphase_type"]),
                    "solverType": ("Token", policy["solver_type"]),
                    "timeStepsPerSecond": ("UInt", policy["physics_hz"]),
                    "enableExternalForcesEveryIteration": ("Bool", True)}
    for name, (kind, item) in scene_values.items():
        scene.CreateAttribute("physxScene:" + name, getattr(Sdf.ValueTypeNames, kind), custom=False).Set(item)
    UsdPhysics.Scene(scene).CreateGravityMagnitudeAttr(policy["gravity_magnitude"])
    physics_report["scene"]["gravity_m_s2"] = policy["gravity_magnitude"]
    physics_report["scene"]["rigid_chain_policy"] = deepcopy(policy)
    # Apply external forces uniformly across TGS substeps. Native velocities and
    # equilibrium accuracy still require independent runtime convergence checks.
    physics_report["scene"]["rigid_chain_runtime_policy"] = {"external_forces_every_iteration": True}
    if resource_usage is not None:
        physics_report["scene"]["rigid_chain_resources"] = deepcopy(resource_usage)
    stage.SetTimeCodesPerSecond(float(policy["physics_hz"]))
    stage.SetFramesPerSecond(60.0)
    scope = physics_report["physics_root"] + "/RigidChains"
    UsdGeom.Scope.Define(stage, scope)
    output, assigned_actor_parameters = [], {}

    def vec(item):
        return Gf.Vec3f(*map(float, item))

    def quat(item):
        return Gf.Quatf(float(item.GetReal()), vec(item.GetImaginary()))

    def local_point(path, world):
        if not path:
            return vec(world)
        xf = UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(path))
        return vec(xf.GetInverse().Transform(Gf.Vec3d(*map(float, world))))

    def local_rotation(path, rotation):
        if not path:
            return quat(rotation)
        current = UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(path)).ExtractRotationQuat()
        return quat(current.GetInverse() * rotation)

    for chain_id, declaration in sorted(declarations.items()):
        target = declaration["target_node_id"]
        if target not in by_node:
            raise ValueError(f"Rigid chain occurrence is missing: {target}")
        path = scope + "/Chain_" + hashlib.sha256(chain_id.encode("utf-8")).hexdigest()
        if stage.GetPrimAtPath(path):
            raise ValueError(f"Rigid chain destination collision: {chain_id!r}")
        plan = plan_chain(declaration)
        points = np.asarray(plan["points_world_m"])
        root = UsdGeom.Xform.Define(stage, path).GetPrim()
        root.SetDisplayName(chain_id)
        root.CreateAttribute("sca:chain_id", Sdf.ValueTypeNames.String).Set(chain_id)
        root.CreateAttribute("sca:representation", Sdf.ValueTypeNames.Token).Set(REPRESENTATION)
        root.CreateAttribute("sca:target_node_id", Sdf.ValueTypeNames.String).Set(target)
        root.CreateAttribute("sca:physicsDeclaration", Sdf.ValueTypeNames.String).Set(json.dumps(declaration, sort_keys=True, ensure_ascii=False, allow_nan=False))
        root.CreateRelationship("sca:sourceOccurrence").SetTargets([Sdf.Path(by_node[target]["prim_path"])])
        parent = by_node[declaration["parent_node_id"]]["prim_path"]
        root.CreateRelationship("sca:parentOccurrence").SetTargets([Sdf.Path(parent)])
        stage.GetPrimAtPath(parent).CreateRelationship("sca:rigidJointChains").AddTarget(root.GetPath())
        semantic = stage.GetPrimAtPath(by_node[target]["prim_path"])
        UsdGeom.Imageable(semantic).CreateVisibilityAttr("invisible")
        semantic.CreateAttribute("sca:physical_type", Sdf.ValueTypeNames.Token).Set(REPRESENTATION)
        semantic.CreateRelationship("sca:rigidJointChain").SetTargets([root.GetPath()])
        material_path = path + "/ContactMaterial"
        material = UsdPhysics.MaterialAPI.Apply(UsdShade.Material.Define(stage, material_path).GetPrim())
        material.CreateStaticFrictionAttr(value(declaration["contact"]["static_friction"]))
        material.CreateDynamicFrictionAttr(value(declaration["contact"]["dynamic_friction"]))
        material.CreateRestitutionAttr(value(declaration["contact"]["restitution"]))
        UsdGeom.Scope.Define(stage, path + "/Links")
        UsdGeom.Scope.Define(stage, path + "/Joints")
        authored = []

        def attribute(prim, name, kind, item):
            prop = prim.CreateAttribute(name, getattr(Sdf.ValueTypeNames, kind), custom=False)
            if not prop.Set(item):
                raise ValueError(f"Cannot author {prim.GetPath()}.{name}")
            authored.append({"prim_path": str(prim.GetPath()), "attribute": name,
                             "type": str(prop.GetTypeName()), "value": _plain(prop.Get())})

        def apply_body(prim, *, actor=False):
            parameters = declaration["body"]
            selected = ("solver_position_iterations", "solver_velocity_iterations", "enable_speculative_ccd") if actor else (
                "linear_damping", "angular_damping", "solver_position_iterations", "solver_velocity_iterations", "enable_speculative_ccd")
            specs = {"linear_damping": ("linearDamping", "Float"), "angular_damping": ("angularDamping", "Float"),
                     "solver_position_iterations": ("solverPositionIterationCount", "Int"),
                     "solver_velocity_iterations": ("solverVelocityIterationCount", "Int"),
                     "enable_speculative_ccd": ("enableSpeculativeCCD", "Bool")}
            requests = {field: value(parameters[field]) for field in selected}
            if actor:
                identity = str(prim.GetPath())
                if identity in assigned_actor_parameters and assigned_actor_parameters[identity] != requests:
                    raise ValueError(f"Rigid chains request conflicting endpoint actor solver/CCD settings: {identity}")
                assigned_actor_parameters[identity] = requests
            prim.AddAppliedSchema("PhysxRigidBodyAPI")
            for field, item in requests.items():
                suffix, kind = specs[field]
                attribute(prim, "physxRigidBody:" + suffix, kind, item)

        links, rotations = [], []
        for index, (p0, p1, arc, chord, radius, mass, inertia) in enumerate(zip(
                points[:-1], points[1:], plan["arc_lengths_m"], plan["chord_lengths_m"],
                plan["radii_m"], plan["masses_kg"], plan["inertia_kg_m2"])):
            rotation = Gf.Rotation(Gf.Vec3d(1, 0, 0), Gf.Vec3d(*map(float, (p1 - p0) / chord))).GetQuat()
            rotations.append(rotation)
            shape = UsdGeom.Capsule.Define(stage, f"{path}/Links/link_{index:04d}")
            prim = shape.GetPrim()
            shape.CreateAxisAttr("X")
            shape.CreateRadiusAttr(radius)
            shape.CreateHeightAttr(chord)
            shape.CreateExtentAttr([vec([-chord/2-radius, -radius, -radius]), vec([chord/2+radius, radius, radius])])
            shape.AddTranslateOp().Set(Gf.Vec3d(*map(float, (p0 + p1) / 2)))
            shape.AddOrientOp().Set(quat(rotation))
            shape.CreateDisplayColorAttr([vec([.025, .027, .03])])
            body = UsdPhysics.RigidBodyAPI.Apply(prim)
            body.CreateRigidBodyEnabledAttr(True)
            body.CreateKinematicEnabledAttr(False)
            body.CreateVelocityAttr(vec([0, 0, 0]))
            body.CreateAngularVelocityAttr(vec([0, 0, 0]))
            mass_api = UsdPhysics.MassAPI.Apply(prim)
            mass_api.CreateMassAttr(mass)
            mass_api.CreateCenterOfMassAttr(vec([0, 0, 0]))
            mass_api.CreateDiagonalInertiaAttr(vec(inertia))
            mass_api.CreatePrincipalAxesAttr(Gf.Quatf(1))
            UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(value(declaration["collision"]["enabled"]))
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(UsdShade.Material.Get(stage, material_path), materialPurpose="physics")
            apply_body(prim)
            prim.AddAppliedSchema("PhysxCollisionAPI")
            attribute(prim, "physxCollision:contactOffset", "Float", value(declaration["collision"]["contact_offset"]))
            attribute(prim, "physxCollision:restOffset", "Float", value(declaration["collision"]["rest_offset"]))
            links.append({"path": str(prim.GetPath()), "index": index,
                          "arc_start_m": plan["stations_m"][index], "arc_end_m": plan["stations_m"][index+1],
                          "arc_length_m": arc, "chord_length_m": chord, "radius_m": radius, "mass_kg": mass,
                          "endpoint_0_m": p0.tolist(), "endpoint_1_m": p1.tolist(), "center_m": ((p0+p1)/2).tolist(),
                          "orientation_wxyz": _plain(quat(rotation)), "diagonal_inertia_kg_m2": inertia})
        semantic.CreateRelationship("sca:physicsBodies").SetTargets([Sdf.Path(link["path"]) for link in links])
        semantic.CreateRelationship("sca:physicsGeometry").SetTargets([Sdf.Path(link["path"]) for link in links])
        semantic.CreateRelationship("sca:renderGeometry").SetTargets([Sdf.Path(link["path"]) for link in links])
        joints = []

        def make_joint(name, body0, body1, anchor, rot0, rot1, *, fixed=False, **extra):
            joint = (UsdPhysics.FixedJoint if fixed else UsdPhysics.Joint).Define(stage, path + "/Joints/" + name)
            joint.CreateBody0Rel().SetTargets([Sdf.Path(body0)] if body0 else [])
            joint.CreateBody1Rel().SetTargets([Sdf.Path(body1)] if body1 else [])
            joint.CreateLocalPos0Attr(local_point(body0, anchor))
            joint.CreateLocalPos1Attr(local_point(body1, anchor))
            joint.CreateLocalRot0Attr(rot0)
            joint.CreateLocalRot1Attr(rot1)
            joint.CreateCollisionEnabledAttr(False)
            joint.CreateExcludeFromArticulationAttr(True)
            record = {"path": str(joint.GetPath()), "type": joint.GetPrim().GetTypeName(),
                      "kind": "endpoint_fixed" if fixed else "internal_d6", "body0": [body0] if body0 else [],
                      "body1": [body1] if body1 else [], "local_position_0_m": _plain(joint.GetLocalPos0Attr().Get()),
                      "local_position_1_m": _plain(joint.GetLocalPos1Attr().Get()),
                      "local_rotation_0_wxyz": _plain(joint.GetLocalRot0Attr().Get()),
                      "local_rotation_1_wxyz": _plain(joint.GetLocalRot1Attr().Get()), "collision_enabled": False, **extra}
            joints.append(record)
            return joint, record

        for index in range(len(links)-1):
            joint, record = make_joint(f"joint_{index:04d}", links[index]["path"], links[index+1]["path"],
                                       points[index+1], Gf.Quatf(1), Gf.Quatf(1))
            for axis in ("transX", "transY", "transZ"):
                limit = UsdPhysics.LimitAPI.Apply(joint.GetPrim(), axis)
                limit.CreateLowAttr(1.0)
                limit.CreateHighAttr(-1.0)
            ell = (plan["arc_lengths_m"][index] + plan["arc_lengths_m"][index+1]) / 2
            bend = min(value(declaration["limits"]["maximum_bend_angle"]), math.degrees(ell / value(declaration["limits"]["minimum_bend_radius"])))
            drives = {}
            for axis in ("rotX", "rotY", "rotZ"):
                twist = axis == "rotX"
                angle = value(declaration["limits"]["maximum_twist_angle"]) if twist else bend
                limit = UsdPhysics.LimitAPI.Apply(joint.GetPrim(), axis)
                limit.CreateLowAttr(-angle)
                limit.CreateHighAttr(angle)
                stiffness = value(declaration["mechanics"]["torsional_rigidity" if twist else "bending_rigidity"]) / ell
                damping = value(declaration["mechanics"]["joint_damping"])
                drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), axis)
                drive.CreateTypeAttr("force")
                drive.CreateStiffnessAttr(stiffness * math.pi / 180)
                drive.CreateDampingAttr(damping * math.pi / 180)
                drive.CreateTargetPositionAttr(0.0)
                drive.CreateTargetVelocityAttr(0.0)
                drive.CreateMaxForceAttr(float("inf"))
                drives[axis] = {"limit_deg": [-angle, angle], "stiffness_SI_N_m_rad": stiffness,
                                "damping_SI_N_m_s_rad": damping, "stiffness_USD_SI_per_degree": stiffness*math.pi/180,
                                "damping_USD_SI_per_degree": damping*math.pi/180, "target_deg": 0.0}
            record.update(angular_drives=drives, translations_locked=True, material_mean_length_m=ell,
                          rest_state="straight segment axes; initial CAD curvature is elastically loaded")
        endpoints, endpoint_filters = [], []
        for endpoint, link_index, rotation, point in (("start", 0, rotations[0], points[0]),
                                                      ("end", len(links)-1, rotations[-1], points[-1])):
            declared = declaration["endpoints"][endpoint]
            mode = declared["mode"]
            entry = {"endpoint": endpoint, "mode": mode, "world_position_m": point.tolist(),
                     "world_rotation_wxyz": _plain(quat(rotation)), "rigid_body_path": None, "joint_path": None}
            if mode == "free":
                endpoints.append(entry)
                continue
            actor, endpoint_shapes = None, []
            if mode == "body_fixed":
                node = declared["rigid_node_id"]
                if node not in groups:
                    raise ValueError(f"Rigid chain endpoint does not resolve to a geometric rigid group: {node}")
                group = groups[node]
                entry.update(rigid_node_id=node, rigid_body_mode=group["mode"], rigid_group_path=group["body_path"])
                endpoint_shapes = [part["shape_path"] for part in group["parts"]]
                actor = group["body_path"] if group["mode"] != "static" else None
                entry["rigid_body_path"] = actor
                if actor and value(declaration["body"]["apply_to_endpoints"]):
                    apply_body(stage.GetPrimAtPath(actor), actor=True)
            first = (actor, links[link_index]["path"]) if endpoint == "start" else (links[link_index]["path"], actor)
            rotations_pair = (local_rotation(actor, rotation), Gf.Quatf(1)) if endpoint == "start" else (Gf.Quatf(1), local_rotation(actor, rotation))
            joint, rec = make_joint("attachment_" + endpoint, *first, point, *rotations_pair, fixed=True, endpoint=endpoint)
            entry["joint_path"] = rec["path"]
            entry["collider_paths"] = endpoint_shapes
            if value(declaration["collision"]["filter_endpoint_links"]):
                endpoint_filters.extend((links[link_index]["path"], shape) for shape in endpoint_shapes)
            endpoints.append(entry)
        filter_set = set(endpoint_filters)
        filter_set.update((links[first]["path"], links[second]["path"])
                          for first, second in material_filter_indices(plan, declaration))
        by_source = {}
        for a, b in sorted(filter_set):
            by_source.setdefault(a, []).append(Sdf.Path(b))
        for first, targets in by_source.items():
            UsdPhysics.FilteredPairsAPI.Apply(stage.GetPrimAtPath(first)).CreateFilteredPairsRel().SetTargets(targets)
        # Record the actually represented proxy bounds, including quatf ->
        # Gf.Rotation conversion in USD's orient op. The ideal path remains
        # separately recorded in points_world_m and each material endpoint.
        bounds = []
        bounds_cache = UsdGeom.XformCache()
        for link in links:
            transform = bounds_cache.GetLocalToWorldTransform(stage.GetPrimAtPath(link["path"]))
            endpoints_actual = np.asarray([transform.Transform(Gf.Vec3d(x, 0, 0))
                                          for x in (-link["chord_length_m"]/2, link["chord_length_m"]/2)])
            bounds.append([endpoints_actual.min(axis=0)-link["radius_m"], endpoints_actual.max(axis=0)+link["radius_m"]])
        minimum = np.min(np.asarray(bounds)[:, 0], axis=0)
        maximum = np.max(np.asarray(bounds)[:, 1], axis=0)
        output.append({"chain_id": chain_id, "representation": REPRESENTATION, "prim_path": path,
                       "target_node_id": target, "target_occurrence_path": by_node[target]["prim_path"],
                       "parent_occurrence_path": parent, "geometry_source": deepcopy(declaration["geometry_source"]),
                       "declaration": deepcopy(declaration), "stations_m": plan["stations_m"], "points_world_m": plan["points_world_m"],
                       "links": links, "joints": joints, "endpoints": endpoints,
                       "filtered_pairs": [list(pair) for pair in sorted(filter_set)], "authored_properties": authored,
                       "material_neighbor_filter": {"enabled": value(declaration["collision"].get("filter_material_neighbors", False)),
                           "criterion": "material interval gap <= sum of capsule radii (plus 1e-12 m numerical tolerance)",
                           "purpose": "remove local capsule-cap overlap under refinement, preserving distant material self-contact"},
                       "material_path": material_path, "bounds_world_m": [minimum.tolist(), maximum.tolist()],
                       "total_arc_length_m": sum(plan["arc_lengths_m"]), "total_chord_length_m": sum(plan["chord_lengths_m"]),
                       "total_mass_kg": sum(plan["masses_kg"]), "natural_state": "straight_local_axes",
                       "damping_mode": "per_joint", "initial_shape": "planar", "render": "rigid capsules",
                       "mass_policy": "linear_density times material arc length; capsule overlap adds no mass",
                       "capsule_height_semantics": "height is centerline chord; hemispheres extend beyond stations",
                       "parameter_status": "Persisted provenance controls parameter evidence; no automatic real-wire calibration",
                       "simulation_verified": False})
        if "resolution" in declaration:
            output[-1]["resolved_configuration"] = deepcopy(declaration["resolution"])
        if resource_usage is not None:
            output[-1]["resource_usage"] = deepcopy(resource_usage)
    physics_report["body_count"] += sum(len(item["links"]) for item in output)
    physics_report["dynamic_body_count"] += sum(len(item["links"]) for item in output)
    physics_report["collider_count"] += sum(len(item["links"]) for item in output)
    physics_report["joint_count"] += sum(len(item["joints"]) for item in output)
    return output
