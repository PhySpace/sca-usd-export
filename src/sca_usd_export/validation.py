"""Reopen USD and check geometry, hierarchy, transformations and physics data."""

import json
from pathlib import Path

import numpy as np

from .dependencies import require_usd
from .history import read_history_bundle
from .model import geometry_digest, transformed_points, validate_triangles


def validate_usd(path, *, expected=None):
    Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt = require_usd()
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"USD file does not exist: {path}")
    if expected is None:
        expected = json.loads(
            path.with_suffix(".report.json").read_text(encoding="utf-8")
        )
    stage = Usd.Stage.Open(str(path))
    if not stage:
        raise ValueError(f"OpenUSD cannot load {path}")
    if not stage.GetDefaultPrim() or str(stage.GetDefaultPrim().GetPath()) != "/Model":
        raise ValueError("USD defaultPrim must be /Model")
    actual_mpu = expected['configuration'].get('meters_per_unit', 1.0)
    if not UsdGeom.StageHasAuthoredMetersPerUnit(stage) or UsdGeom.GetStageMetersPerUnit(stage) != actual_mpu:
        raise ValueError('USD metresPerUnit differs from explicit export configuration')
    if actual_mpu != 1.0:
        from .stage_units import stage_in_si
        stage = stage_in_si(stage, unit_report=expected.get('stage_units'),
                            geometry_tolerance_m=expected['configuration']['geometry_absolute_tolerance_m'])
    if UsdGeom.GetStageUpAxis(stage) != expected["configuration"]["up_axis"]:
        raise ValueError("USD upAxis differs from configuration")
    if (
        not UsdPhysics.StageHasAuthoredKilogramsPerUnit(stage)
        or UsdPhysics.GetStageKilogramsPerUnit(stage) != 1.0
    ):
        raise ValueError("USD kilogramsPerUnit must be explicitly 1")
    tolerance = expected["configuration"]["geometry_absolute_tolerance_m"]
    cache = UsdGeom.XformCache()
    all_meshes, all_triangles, rigid_bodies, colliders, joints = 0, 0, 0, 0, 0
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if prim.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(prim)
            points = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
            counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), dtype=np.int64)
            indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int64)
            valid, reason = UsdGeom.Mesh.ValidateTopology(
                indices.tolist(), counts.tolist(), len(points)
            )
            if not valid or not np.all(counts == 3):
                raise ValueError(
                    f"{prim.GetPath()}: invalid triangle topology: {reason}"
                )
            validate_triangles(points, indices.reshape((-1, 3)))
            if mesh.GetSubdivisionSchemeAttr().Get() != UsdGeom.Tokens.none:
                raise ValueError(f"{prim.GetPath()}: subdivisionScheme must be none")
            bounds = np.array([points.min(axis=0), points.max(axis=0)])
            if not np.allclose(
                np.asarray(mesh.GetExtentAttr().Get()), bounds, rtol=0, atol=tolerance
            ):
                raise ValueError(f"{prim.GetPath()}: extent differs from actual points")
            all_meshes += 1
            all_triangles += len(counts)
        from .mechanical_properties import applied_schema_tokens
        if prim.HasAPI(UsdPhysics.CollisionAPI) and "OmniPhysicsVolumeDeformableSimAPI" not in applied_schema_tokens(prim):
            colliders += 1
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid_bodies += 1
            mass = UsdPhysics.MassAPI(prim)
            if (
                not mass
                or mass.GetMassAttr().Get() is None
                or mass.GetMassAttr().Get() <= 0
            ):
                raise ValueError(
                    f"{prim.GetPath()}: dynamic body needs explicit positive mass"
                )
            diagonal = np.asarray(mass.GetDiagonalInertiaAttr().Get())
            if (
                diagonal.shape != (3,)
                or not np.isfinite(diagonal).all()
                or np.any(diagonal <= 0)
            ):
                raise ValueError(f"{prim.GetPath()}: invalid principal inertia")
        if prim.IsA(UsdPhysics.Joint):
            joints += 1
            joint = UsdPhysics.Joint(prim)
            for relation in (joint.GetBody0Rel(), joint.GetBody1Rel()):
                for target in relation.GetTargets():
                    if not stage.GetPrimAtPath(target):
                        raise ValueError(f"joint target does not exist: {target}")

    physics_expected = expected["physics"]
    if "geometry_tolerance_policy" in expected:
        policy = expected["geometry_tolerance_policy"]
        saved = {entry["chain_id"]: entry["declaration"]["resolution"]["solver"]["export_geometry_absolute_tolerance_m"]
                 for entry in physics_expected.get("rigid_chains", [])
                 if "export_geometry_absolute_tolerance_m" in entry["declaration"].get("resolution", {}).get("solver", {})}
        if (not isinstance(policy, dict) or policy.get("unit") != "m"
                or policy.get("value") != tolerance or policy.get("saved_recommendations_m") != saved
                or policy.get("origin") not in {"explicit_export_configuration", "saved_cable_resolution", "centralized_export_default"}):
            raise ValueError("Export geometry tolerance policy changed")
        if policy["origin"] == "saved_cable_resolution" and (not saved or tolerance < max(saved.values())):
            raise ValueError("Saved export geometry recommendation was not respected")
    from .physics_validation import validate_physics
    physics_values = validate_physics(stage, physics_expected, tolerance=tolerance)
    if physics_expected.get("cables"):
        from .native_cables import validate_cables
        physics_values["native_cables"] = validate_cables(stage, physics_expected["cables"], tolerance=tolerance, physics_report=physics_expected)
    if physics_expected.get('rigid_chains'):
        from .rigid_chain_validation import validate_rigid_chains
        physics_values['rigid_chains'] = validate_rigid_chains(stage, physics_expected['rigid_chains'], physics_expected, tolerance=tolerance)
    if (
        rigid_bodies != physics_expected.get("body_count", physics_expected["dynamic_body_count"])
        or colliders != physics_expected["collider_count"]
        or joints != physics_expected["joint_count"]
    ):
        raise ValueError("physics body/collider/joint count changed")
    parsed = UsdPhysics.UsdPhysicsLoadStageFromPrimRange(
        stage, [Sdf.Path(physics_expected["physics_root"])]
    )
    parsed_summary = {}
    for kind, (paths, descriptors) in parsed.items():
        if any(not descriptor.isValid for descriptor in descriptors):
            raise ValueError(f"USD Physics parser found an invalid {kind}")
        parsed_summary[str(kind)] = len(paths)
    mesh_descriptors = parsed.get(UsdPhysics.ObjectType.MeshShape, ([], []))[1]
    chain_shape_count = sum(len(chain['links']) for chain in physics_expected.get('rigid_chains', []))
    if len(mesh_descriptors) != physics_expected["collider_count"] - chain_shape_count:
        raise ValueError("USD Physics parser collider count differs from report")
    capsule_descriptors = parsed.get(UsdPhysics.ObjectType.CapsuleShape, ([], []))[1]
    if len(capsule_descriptors) != chain_shape_count:
        raise ValueError('USD Physics parser capsule count differs from rigid chain declarations')

    for group in physics_expected["groups"]:
        body = stage.GetPrimAtPath(group["body_path"])
        mode = group.get("mode", "dynamic" if group["dynamic"] else "static")
        if not body or body.HasAPI(UsdPhysics.RigidBodyAPI) != (mode != "static"):
            raise ValueError("physics group rigid-body mapping changed")
        if mode != "static":
            rigid = UsdPhysics.RigidBodyAPI(body)
            if not rigid.GetRigidBodyEnabledAttr().Get() or rigid.GetKinematicEnabledAttr().Get() != (mode == "kinematic"):
                raise ValueError("physics dynamic body enabled/kinematic configuration changed")
        mass = UsdPhysics.MassAPI(body)
        if not mass or not np.isclose(
            mass.GetMassAttr().Get(), group["mass_kg"], rtol=2e-6
        ):
            raise ValueError("physics mass changed")
        if not np.allclose(
            mass.GetCenterOfMassAttr().Get(),
            # USD MassAPI fixes centerOfMass to point3f. Compare with the
            # correctly rounded authored value rather than an unrepresentable
            # double-precision CAD value; CAD geometry tolerance is unchanged.
            np.asarray(group["center_of_mass_body_m"], dtype=np.float32),
            rtol=0,
            atol=tolerance,
        ):
            raise ValueError("physics centre of mass changed")
        if not np.allclose(
            mass.GetDiagonalInertiaAttr().Get(),
            group["principal_inertia_kg_m2"],
            rtol=2e-6,
            atol=1e-20,
        ):
            raise ValueError("physics principal inertia changed")
    by_id = {record["node_id"]: record for record in expected["occurrences"]}
    source_by_id = {
        record["node_id"]: record
        for record in expected["from_sca"]["occurrence_graph"]["nodes"]
    }
    source_world_points = []
    semantic_triangle_count = 0
    max_transform_error, max_bounds_error = 0.0, 0.0
    for record in expected["occurrences"]:
        prim = stage.GetPrimAtPath(record["prim_path"])
        if not prim or prim.GetAttribute("sca:node_id").Get() != record["node_id"]:
            raise ValueError(f"missing or changed occurrence: {record['node_id']}")
        if prim.GetDisplayName() != record["display_name"]:
            raise ValueError(f"display name changed: {record['node_id']}")
        source = source_by_id[record["node_id"]]
        identity = {
            key: source.get(key)
            for key in ("definition_id", "component_id", "instance_id", "node_kind")
        }
        identity.update(source.get("properties", {}))
        for key, value in identity.items():
            if value is not None and prim.GetAttribute("sca:" + key).Get() != str(
                value
            ):
                raise ValueError(
                    f"component identity changed ({key}): {record['node_id']}"
                )
        parent = record["parent_node_id"]
        expected_parent = by_id[parent]["prim_path"] if parent else "/Model"
        if str(prim.GetParent().GetPath()) != expected_parent:
            raise ValueError(f"component hierarchy changed: {record['node_id']}")
        matrix = np.asarray(cache.GetLocalToWorldTransform(prim)).T
        local = np.asarray(UsdGeom.Xformable(prim).GetLocalTransformation()).T
        error = float(np.max(np.abs(matrix - np.asarray(record["world_matrix_m"]))))
        max_transform_error = max(max_transform_error, error)
        if error > tolerance or not np.allclose(
            local, record["local_matrix_m"], rtol=0, atol=tolerance
        ):
            raise ValueError(f"local/world transform changed: {record['node_id']}")
        if record["kind"] == "part":
            mesh = UsdGeom.Mesh(stage.GetPrimAtPath(record["mesh_path"]))
            points = np.asarray(mesh.GetPointsAttr().Get())
            indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape((-1, 3))
            if geometry_digest(points, indices) != record["geometry_sha256"]:
                raise ValueError(f"geometry changed: {record['node_id']}")
            if (
                len(indices) != record["triangle_count"]
                or len(points) != record["vertex_count"]
            ):
                raise ValueError(f"mesh counts changed: {record['node_id']}")
            semantic_triangle_count += len(indices)
            world = transformed_points(
                points, np.asarray(cache.GetLocalToWorldTransform(mesh.GetPrim())).T
            )
            bounds = np.array([world.min(axis=0), world.max(axis=0)])
            max_bounds_error = max(
                max_bounds_error,
                float(np.max(np.abs(bounds - record["bounds_world_m"]))),
            )
            if max_bounds_error > tolerance:
                raise ValueError(f"mesh bounds changed: {record['node_id']}")
            source_world_points.append(world)
            rigid_chain = next((chain for chain in physics_expected.get('rigid_chains', []) if chain['target_node_id'] == record['node_id']), None)
            if rigid_chain is not None:
                # Original BREP identity/pose is checked above. The replacing
                # capsule geometry, backlinks and mass are checked independently.
                continue
            target = next((cable for cable in physics_expected.get('cables', []) if cable.get('target_node_id') == record['node_id']), None)
            if target is not None:
                if UsdGeom.Imageable(prim).ComputeVisibility() != 'invisible':
                    raise ValueError('CAD deformable semantic source must be hidden; render skin is the sole visible representation')
                relation = prim.GetRelationship('sca:physicsGeometry')
                if [str(value) for value in relation.GetTargets()] != [target['simulation_mesh_path']]:
                    raise ValueError('CAD deformable physics geometry backlink changed')
                relation = prim.GetRelationship('sca:renderGeometry')
                if [str(value) for value in relation.GetTargets()] != [target['render_mesh_path']]:
                    raise ValueError('CAD deformable render geometry backlink changed')
                continue
            # Physics render/collision shape must occupy exactly the same solved pose.
            shape_rel = prim.GetRelationship(
                "sca:physicsGeometry"
            ) or prim.GetRelationship("simplecad:physicsGeometry")
            if not shape_rel or len(shape_rel.GetTargets()) != 1:
                raise ValueError(
                    f"part has no unique physics geometry: {record['node_id']}"
                )
            shape = UsdGeom.Mesh(stage.GetPrimAtPath(shape_rel.GetTargets()[0]))
            if not shape:
                raise ValueError("physics geometry target is not a Mesh")
            physical_points = np.asarray(shape.GetPointsAttr().Get())
            physical_indices = np.asarray(
                shape.GetFaceVertexIndicesAttr().Get()
            ).reshape((-1, 3))
            if (
                geometry_digest(physical_points, physical_indices)
                != record["geometry_sha256"]
            ):
                raise ValueError(f"physics geometry changed: {record['node_id']}")
            if not shape.GetPrim().HasAPI(UsdPhysics.CollisionAPI):
                raise ValueError(f"physics collider API missing: {record['node_id']}")
            physical_world = transformed_points(
                physical_points,
                np.asarray(cache.GetLocalToWorldTransform(shape.GetPrim())).T,
            )
            if not np.allclose(physical_world, world, rtol=0, atol=tolerance):
                raise ValueError(
                    f"physics/semantic geometry poses differ: {record['node_id']}"
                )
            if UsdGeom.Imageable(shape).ComputeVisibility() == UsdGeom.Tokens.invisible:
                raise ValueError(
                    f"physics render geometry is unexpectedly invisible: {record['node_id']}"
                )

    combined = np.concatenate(source_world_points)
    bounds = np.array([combined.min(axis=0), combined.max(axis=0)])
    if not np.allclose(
        bounds, expected["calculated"]["bounds_world_m"], rtol=0, atol=tolerance
    ):
        raise ValueError("world bounds differ from evaluated SCA meshes")
    scene_bounds = bounds.copy()
    for cable in physics_expected.get("cables",[]):
        # The native simulation boundary can extend slightly beyond an actual
        # CAD display surface after official cooking. Complete physical bounds
        # include both representations; they need not share vertices/bounds.
        for mesh_path in {cable['simulation_mesh_path'], cable.get('render_mesh_path',cable['simulation_mesh_path'])}:
            prim = stage.GetPrimAtPath(mesh_path)
            points = np.asarray(prim.GetAttribute("points").Get())
            points = transformed_points(points,np.asarray(cache.GetLocalToWorldTransform(prim)).T)
            scene_bounds[0] = np.minimum(scene_bounds[0],points.min(axis=0))
            scene_bounds[1] = np.maximum(scene_bounds[1],points.max(axis=0))
    for chain in physics_expected.get('rigid_chains', []):
        # validate_rigid_chains already recomputed these bounds from the reopened
        # Capsule dimensions and poses, rather than trusting a points-only mesh.
        scene_bounds[0] = np.minimum(scene_bounds[0], chain['bounds_world_m'][0])
        scene_bounds[1] = np.maximum(scene_bounds[1], chain['bounds_world_m'][1])
    if "scene_bounds_world_m" in expected["calculated"] and not np.allclose(scene_bounds,expected["calculated"]["scene_bounds_world_m"],rtol=0,atol=tolerance):
        raise ValueError("complete scene bounds differ from CAD and native cable geometry")
    if semantic_triangle_count != expected["calculated"]["triangle_count"]:
        raise ValueError("triangle count differs from SCA tessellation")
    history_attr = stage.GetDefaultPrim().GetAttribute("sca:history").Get()
    history = read_history_bundle(path.parent / history_attr.path)
    return dict(
        ok=True,
        physics_static_ok=True,
        physics_values=physics_values,
        simulator_validated=False,
        default_prim="/Model",
        metres_per_unit=actual_mpu,
        kilograms_per_unit=1.0,
        up_axis=str(UsdGeom.GetStageUpAxis(stage)),
        semantic_occurrence_count=len(expected["occurrences"]),
        part_occurrence_count=len(source_world_points),
        composed_mesh_count=all_meshes,
        composed_triangle_count=all_triangles,
        part_triangle_count=semantic_triangle_count,
        collision_shape_count=colliders,
        dynamic_rigid_body_count=rigid_bodies,
        physics_joint_count=joints,
        physics_parser_objects=parsed_summary,
        max_transform_error=max_transform_error,
        max_bounds_error_m=max_bounds_error,
        bounds_world_m=bounds.tolist(),
        dimensions_m=(bounds[1] - bounds[0]).tolist(),
        cad_bounds_world_m=bounds.tolist(),
        scene_bounds_world_m=scene_bounds.tolist(),
        scene_dimensions_m=(scene_bounds[1]-scene_bounds[0]).tolist(),
        history_definition_count=len(history["definitions"]),
    )
