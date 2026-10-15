"""Author standard USD Physics from explicit SCA assembly relationships.

The semantic component tree stays intact. Rendered meshes and colliders live
under sibling physical groups so a handle may remain a separate component while
moving as part of its door's rigid body. All record matrices use column vectors
and metres; conversion to USD's row-vector convention happens only at authoring.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict, deque
from typing import Any


def _name(prefix: str, identity: str) -> str:
    return prefix + "_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


class _Groups:
    def __init__(self, node_ids: list[str]) -> None:
        self.parent = {node_id: node_id for node_id in node_ids}

    def find(self, node_id: str) -> str:
        current = node_id
        while self.parent[current] != current:
            current = self.parent[current]
        while node_id != current:
            previous = self.parent[node_id]
            self.parent[node_id] = current
            node_id = previous
        return current

    def union(self, first: str, second: str) -> None:
        roots = sorted((self.find(first), self.find(second)))
        self.parent[roots[1]] = roots[0]


def _positive(value: Any, description: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{description} must be positive and finite")
    return number


def _density(part: Any, config: Any) -> tuple[float, str]:
    material = getattr(part, "material", None)
    if material is None or material.density is None:
        return (
            _positive(config.default_density_kg_m3, "default_density_kg_m3"),
            "config.default_density_kg_m3",
        )
    factors = {
        "kg/mm^3": 1e9,
        "kg/m^3": 1.0,
        "kg/m3": 1.0,
        "g/cm^3": 1000.0,
        "g/cm3": 1000.0,
    }
    if material.density_unit not in factors:
        raise ValueError(f"Unsupported SCA density unit: {material.density_unit!r}")
    value = (
        _positive(material.density, "SCA material density")
        * factors[material.density_unit]
    )
    return value, f"SCA Material {material.material_id} ({material.density_unit})"


def _brep_inertia(record: dict[str, Any], density: float) -> Any:
    """OCCT returns central inertia for unit density in source mm^5."""
    import numpy as np
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    # CGFlag/IFlag default to False: requesting volume alone yields zero inertia.
    error = BRepGProp.VolumePropertiesGK_s(
        record["item"].body.wrapped, props, 1e-9, True, True, True, True
    )
    if not math.isfinite(error) or error < 0:
        raise ValueError(
            f"OCCT could not integrate mass properties for {record['node_id']!r}"
        )
    if not math.isfinite(props.Mass()) or props.Mass() <= 0:
        raise ValueError(
            f"Part {record['node_id']!r} has no positive BREP volume for mass properties"
        )
    inertia = props.MatrixOfInertia()
    result = np.array(
        [[inertia.Value(row, col) for col in range(1, 4)] for row in range(1, 4)],
        dtype=float,
    )
    result *= density * 1e-15
    if not np.isfinite(result).all():
        raise ValueError(f"Non-finite BREP inertia for {record['node_id']!r}")
    return result


def _frame_matrix(payload: Any) -> Any:
    import numpy as np
    from simplecadapi.product.placement import placement_from_canonical

    frame = placement_from_canonical(payload)
    result = np.eye(4)
    result[:3, :3] = np.column_stack((frame.x_axis, frame.y_axis, frame.z_axis))
    result[:3, 3] = np.asarray(frame.origin) * 0.001
    return result


def apply_physics(
    stage: Any, records: list[dict[str, Any]], occurrence_graph: Any, config: Any
) -> dict[str, Any]:
    """Add physical groups, collision meshes, masses and supported SCA joints.

    ``records`` contains one item per actual occurrence (including assemblies),
    not one per definition. Required geometry fields are ``definition_mesh_path``,
    ``prim_path``, ``world_matrix``, ``volume_m3``, and
    ``center_of_mass_local_m``. Bodies with unsupported coupling constraints are
    conservatively kept static and this decision is included in the report.
    USD and OCP dependencies are imported only when this function is called.
    """
    import numpy as np

    try:
        from pxr import Gf, Sdf, UsdGeom, UsdPhysics, UsdShade
    except ImportError as exc:
        raise RuntimeError(
            "USD physics export requires usd-core; install the independent extension requirements"
        ) from exc

    if not records:
        raise ValueError("Physics export requires at least one occurrence record")
    if not math.isclose(UsdGeom.GetStageMetersPerUnit(stage), 1.0, abs_tol=1e-12):
        raise ValueError("Physics records use metres; stage metersPerUnit must be 1")
    if config.unconnected_body_policy not in {"static", "dynamic"}:
        raise ValueError("unconnected_body_policy must be 'static' or 'dynamic'")
    allowed_approximations = {
        "none",
        "convexHull",
        "convexDecomposition",
        "boundingCube",
        "boundingSphere",
        "meshSimplification",
    }
    for field in ("dynamic_collision_approximation", "static_collision_approximation"):
        if getattr(config, field) not in allowed_approximations:
            raise ValueError(f"Unsupported {field}: {getattr(config, field)!r}")
    if config.dynamic_collision_approximation == "none":
        raise ValueError(
            "Dynamic triangle-mesh collision is not portable; choose a convex collision approximation"
        )
    if config.joint_limit_policy not in {"unbounded", "error"}:
        raise ValueError("joint_limit_policy must be 'unbounded' or 'error'")
    friction = float(config.default_friction)
    restitution = float(config.default_restitution)
    if not math.isfinite(friction) or friction < 0:
        raise ValueError("default_friction must be finite and nonnegative")
    if not math.isfinite(restitution) or not 0 <= restitution <= 1:
        raise ValueError("default_restitution must be in [0, 1]")
    gravity = float(config.gravity_m_s2)
    if not math.isfinite(gravity) or gravity < 0:
        raise ValueError("gravity_m_s2 must be finite and nonnegative")
    if not isinstance(config.joint_collision_enabled, bool):
        raise ValueError("joint_collision_enabled must be bool")

    by_id = {record["node_id"]: record for record in records}
    if len(by_id) != len(records):
        raise ValueError("Duplicate occurrence node_id in physics records")
    for record in records:
        matrix = np.asarray(record["world_matrix"], dtype=float)
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError(f"Invalid world matrix for {record['node_id']!r}")
        if (
            not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-9, rtol=0)
            or not np.allclose(
                matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=2e-6, rtol=0
            )
            or np.linalg.det(matrix[:3, :3]) <= 0
        ):
            raise ValueError(
                f"Physics requires a right-handed rigid transform: {record['node_id']!r}"
            )

    connectors = {
        str(item["connector_snapshot_id"]): item for item in occurrence_graph.connectors
    }

    def leaf_connector(endpoint: Any) -> Any:
        connector_id = str(endpoint["connector_snapshot_id"])
        visited: set[str] = set()
        while True:
            if connector_id in visited:
                raise ValueError(f"Cyclic public connector: {connector_id}")
            visited.add(connector_id)
            if connector_id not in connectors:
                raise ValueError(f"Unresolved connector: {connector_id}")
            value = connectors[connector_id]
            source = value.get("source_connector_snapshot_id")
            if source is None:
                if str(value["node_id"]) not in by_id:
                    raise ValueError(
                        f"Connector occurrence is missing: {value['node_id']}"
                    )
                return value
            connector_id = str(source)

    groups = _Groups(sorted(by_id))
    fixed_records: list[dict[str, Any]] = []
    for edge in occurrence_graph.ground_edges:
        first, second = str(edge["parent_node_id"]), str(edge["child_node_id"])
        if first not in by_id or second not in by_id:
            raise ValueError("Grounding edge references an absent occurrence")
        groups.union(first, second)
        fixed_records.append({"kind": "ground", "a": first, "b": second})
    joint_edges: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for joint in occurrence_graph.joints:
        first, second = leaf_connector(joint["connector_a"]), leaf_connector(
            joint["connector_b"]
        )
        edge = {
            "joint": joint,
            "a": str(first["node_id"]),
            "b": str(second["node_id"]),
            "connector_a": first,
            "connector_b": second,
        }
        kind = str(joint["joint_type"])
        if kind == "fixed":
            groups.union(edge["a"], edge["b"])
            fixed_records.append(
                {
                    "kind": kind,
                    "joint_id": str(joint["joint_id"]),
                    "a": edge["a"],
                    "b": edge["b"],
                }
            )
        else:
            joint_edges.append(edge)
            if kind not in {"revolute", "prismatic"}:
                unsupported.append(
                    {
                        "joint_id": str(joint["joint_id"]),
                        "joint_type": kind,
                        "reason": "Coupling is preserved as SCA history but has no implemented standard USD Physics mapping; connected groups are static",
                    }
                )

    members: dict[str, list[str]] = defaultdict(list)
    for node_id in sorted(by_id):
        members[groups.find(node_id)].append(node_id)
    part_groups = {
        group_id: [by_id[node] for node in nodes if by_id[node]["kind"] == "part"]
        for group_id, nodes in members.items()
    }
    adjacency: dict[str, set[str]] = defaultdict(set)
    frozen_seeds: set[str] = set()
    for edge in joint_edges:
        first, second = groups.find(edge["a"]), groups.find(edge["b"])
        adjacency[first].add(second)
        adjacency[second].add(first)
        if edge["joint"]["joint_type"] not in {"revolute", "prismatic"}:
            frozen_seeds.update((first, second))
        elif first == second:
            frozen_seeds.add(first)
            unsupported.append(
                {
                    "joint_id": str(edge["joint"]["joint_id"]),
                    "joint_type": str(edge["joint"]["joint_type"]),
                    "reason": "Movable joint endpoints lie inside one explicit fixed group; group kept static",
                }
            )

    def reachable(seeds: set[str]) -> set[str]:
        result, pending = set(seeds), deque(sorted(seeds))
        while pending:
            for neighbor in sorted(adjacency[pending.popleft()]):
                if neighbor not in result:
                    result.add(neighbor)
                    pending.append(neighbor)
        return result

    root_group = groups.find(str(occurrence_graph.root_node_id))
    root_is_assembly = by_id[str(occurrence_graph.root_node_id)]["kind"] == "assembly"
    reachable_from_world = reachable({root_group})
    frozen = reachable(frozen_seeds)
    dynamic_groups = {
        group_id
        for group_id, parts in part_groups.items()
        if parts
        and (group_id != root_group or not root_is_assembly)
        and group_id not in frozen
        and (
            (group_id != root_group and group_id in reachable_from_world)
            or config.unconnected_body_policy == "dynamic"
        )
    }
    default_prim = stage.GetDefaultPrim()
    physics_root = (
        str(default_prim.GetPath()) + "/Physics" if default_prim else "/Physics"
    )
    if stage.GetPrimAtPath(physics_root):
        raise ValueError(f"Physics destination already exists: {physics_root}")
    UsdGeom.Xform.Define(stage, physics_root)
    UsdGeom.Scope.Define(stage, physics_root + "/Bodies")
    UsdGeom.Scope.Define(stage, physics_root + "/Joints")
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    scene = UsdPhysics.Scene.Define(stage, physics_root + "/Scene")
    up_axis = str(UsdGeom.GetStageUpAxis(stage))
    scene.CreateGravityDirectionAttr(
        Gf.Vec3f(0, -1, 0) if up_axis == "Y" else Gf.Vec3f(0, 0, -1)
    )
    scene.CreateGravityMagnitudeAttr(gravity)
    material = UsdShade.Material.Define(stage, physics_root + "/DefaultContactMaterial")
    material_api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
    material_api.CreateStaticFrictionAttr(friction)
    material_api.CreateDynamicFrictionAttr(friction)
    material_api.CreateRestitutionAttr(restitution)

    def set_transform(prim: Any, matrix: Any) -> None:
        xform = UsdGeom.Xformable(prim)
        xform.ClearXformOpOrder()
        xform.AddTransformOp().Set(
            Gf.Matrix4d(np.asarray(matrix, dtype=float).T.tolist())
        )

    def quaternion(rotation: Any) -> Any:
        value = (
            Gf.Matrix3d(np.asarray(rotation, dtype=float).T.tolist())
            .ExtractRotation()
            .GetQuat()
        )
        return Gf.Quatf(float(value.GetReal()), Gf.Vec3f(*value.GetImaginary()))

    body_paths: dict[str, str] = {}
    body_frames: dict[str, Any] = {}
    group_records: list[dict[str, Any]] = []
    defaults: list[dict[str, Any]] = [
        {
            "field": "gravity_m_s2",
            "value": gravity,
            "unit": "m/s^2",
            "source": "config.gravity_m_s2",
            "reason": "SCA defines CAD geometry, not a simulation environment",
            "prim_path": str(scene.GetPath()),
        }
    ]
    warnings: list[str] = []
    collider_count = 0
    for group_id in sorted(part_groups):
        parts = part_groups[group_id]
        if not parts:
            continue
        dynamic = group_id in dynamic_groups
        body_path = physics_root + "/Bodies/" + _name("Body", group_id)
        body = UsdGeom.Xform.Define(stage, body_path)
        frame = np.asarray(parts[0]["world_matrix"], dtype=float)
        body_paths[group_id], body_frames[group_id] = body_path, frame
        set_transform(body.GetPrim(), frame)
        body.GetPrim().SetDisplayName(str(parts[0].get("display_name", group_id)))
        body.GetPrim().CreateAttribute(
            "simplecad:groupId", Sdf.ValueTypeNames.String
        ).Set(group_id)
        body.GetPrim().CreateRelationship("simplecad:components").SetTargets(
            [Sdf.Path(by_id[node]["prim_path"]) for node in members[group_id]]
        )
        if dynamic:
            rigid_api = UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
            rigid_api.CreateRigidBodyEnabledAttr(True)
            rigid_api.CreateKinematicEnabledAttr(False)
        reason = (
            "supported_joint_connected_to_ground"
            if dynamic and group_id != root_group and group_id in reachable_from_world
            else (
                "explicit_dynamic_policy"
                if dynamic
                else (
                    "unsupported_relation_frozen"
                    if group_id in frozen
                    else (
                        "grounded"
                        if group_id == root_group and root_is_assembly
                        else "unconnected_static_policy"
                    )
                )
            )
        )
        if reason in {"explicit_dynamic_policy", "unconnected_static_policy"}:
            defaults.append(
                {
                    "field": "unconnected_body_policy",
                    "group_id": group_id,
                    "value": "dynamic" if dynamic else "static",
                    "unit": "policy",
                    "source": "config.unconnected_body_policy",
                    "reason": "SCA has no explicit movable connection from this group to the grounded assembly",
                }
            )
        approximation = (
            config.dynamic_collision_approximation
            if dynamic
            else config.static_collision_approximation
        )
        if dynamic and approximation == "convexHull":
            warnings.append(
                f"{group_id}: convexHull collision can fill holes and concavities; simulator collision cooking remains unverified"
            )
        part_properties: list[dict[str, Any]] = []
        mass_sum = 0.0
        com_sum = np.zeros(3)
        for record in parts:
            source_path = record.get("definition_mesh_path") or record["mesh_path"]
            if not stage.GetPrimAtPath(source_path):
                raise ValueError(f"Referenced definition mesh is absent: {source_path}")
            shape_path = body_path + "/" + _name("Part", record["node_id"])
            shape = stage.DefinePrim(shape_path, "Mesh")
            shape.GetReferences().AddInternalReference(Sdf.Path(source_path))
            shape.SetDisplayName(
                str(
                    record.get(
                        "display_name",
                        getattr(record["item"], "name", None) or record["node_id"],
                    )
                )
            )
            local = np.linalg.inv(frame) @ np.asarray(
                record["world_matrix"], dtype=float
            )
            set_transform(shape, local)
            UsdGeom.Imageable(shape).CreateVisibilityAttr(UsdGeom.Tokens.inherited)
            UsdPhysics.CollisionAPI.Apply(shape).CreateCollisionEnabledAttr(True)
            UsdPhysics.MeshCollisionAPI.Apply(shape).CreateApproximationAttr(
                approximation
            )
            UsdShade.MaterialBindingAPI.Apply(shape).Bind(
                material, materialPurpose="physics"
            )
            density, density_source = _density(record["item"], config)
            for field, value in (
                ("static_friction", friction),
                ("dynamic_friction", friction),
                ("restitution", restitution),
            ):
                defaults.append(
                    {
                        "node_id": record["node_id"],
                        "field": field,
                        "value": value,
                        "unit": "dimensionless",
                        "source": (
                            "config.default_restitution"
                            if field == "restitution"
                            else "config.default_friction"
                        ),
                        "reason": "SCA Material has no contact friction or restitution field",
                    }
                )
            defaults.append(
                {
                    "node_id": record["node_id"],
                    "field": "collision_approximation",
                    "value": approximation,
                    "unit": "policy",
                    "source": (
                        "config.dynamic_collision_approximation"
                        if dynamic
                        else "config.static_collision_approximation"
                    ),
                    "reason": "SCA does not specify a physics collision proxy",
                }
            )
            if density_source.startswith("config."):
                defaults.append(
                    {
                        "node_id": record["node_id"],
                        "field": "density_kg_m3",
                        "value": density,
                        "unit": "kg/m^3",
                        "source": density_source,
                        "reason": "SCA Part has no material density",
                    }
                )
            volume = _positive(
                record["volume_m3"], f"BREP volume of {record['node_id']}"
            )
            mass = density * volume
            com_local = np.asarray(record["center_of_mass_local_m"], dtype=float)
            if com_local.shape != (3,) or not np.isfinite(com_local).all():
                raise ValueError(f"Invalid BREP centre of mass: {record['node_id']}")
            com = (local @ np.append(com_local, 1.0))[:3]
            inertia = _brep_inertia(record, density)
            inertia = local[:3, :3] @ inertia @ local[:3, :3].T
            UsdPhysics.MassAPI.Apply(shape).CreateDensityAttr(density)
            semantic = stage.GetPrimAtPath(record["prim_path"])
            if not semantic:
                raise ValueError(f"Semantic part is absent: {record['prim_path']}")
            UsdGeom.Imageable(semantic).CreateVisibilityAttr(UsdGeom.Tokens.invisible)
            semantic.CreateRelationship("simplecad:physicsBody").SetTargets(
                [Sdf.Path(body_path)]
            )
            semantic.CreateRelationship("simplecad:physicsGeometry").SetTargets(
                [Sdf.Path(shape_path)]
            )
            shape.CreateRelationship("simplecad:sourceComponent").SetTargets(
                [Sdf.Path(record["prim_path"])]
            )
            shape.CreateAttribute("simplecad:nodeId", Sdf.ValueTypeNames.String).Set(
                record["node_id"]
            )
            part_properties.append(
                {
                    "node_id": record["node_id"],
                    "definition_id": record["definition_id"],
                    "semantic_path": record["prim_path"],
                    "shape_path": shape_path,
                    "volume_m3": volume,
                    "density_kg_m3": density,
                    "density_source": density_source,
                    "mass_kg": mass,
                    "_com": com,
                    "_inertia": inertia,
                }
            )
            mass_sum += mass
            com_sum += mass * com
            collider_count += 1
        com = com_sum / mass_sum
        tensor = np.zeros((3, 3))
        for part in part_properties:
            offset = part.pop("_com") - com
            tensor += part.pop("_inertia") + part["mass_kg"] * (
                np.dot(offset, offset) * np.eye(3) - np.outer(offset, offset)
            )
        moments, axes = np.linalg.eigh((tensor + tensor.T) * 0.5)
        if not np.isfinite(moments).all() or np.any(moments <= 0):
            raise ValueError(
                f"Nonpositive principal BREP inertia for group {group_id!r}"
            )
        if np.linalg.det(axes) < 0:
            axes[:, 0] *= -1
        mass_api = UsdPhysics.MassAPI.Apply(body.GetPrim())
        mass_api.CreateMassAttr(mass_sum)
        mass_api.CreateCenterOfMassAttr(Gf.Vec3f(*com))
        mass_api.CreateDiagonalInertiaAttr(Gf.Vec3f(*moments))
        mass_api.CreatePrincipalAxesAttr(quaternion(axes))
        group_records.append(
            {
                "group_id": group_id,
                "body_path": body_path,
                "members": members[group_id],
                "dynamic": dynamic,
                "reason": reason,
                "collision_approximation": approximation,
                "mass_kg": mass_sum,
                "center_of_mass_body_m": com.tolist(),
                "inertia_tensor_body_kg_m2": tensor.tolist(),
                "principal_inertia_kg_m2": moments.tolist(),
                "parts": part_properties,
            }
        )

    joint_records: list[dict[str, Any]] = []
    for edge in joint_edges:
        joint = edge["joint"]
        kind = str(joint["joint_type"])
        first, second = groups.find(edge["a"]), groups.find(edge["b"])
        if kind not in {"revolute", "prismatic"} or first == second:
            continue
        record = {
            "joint_id": str(joint["joint_id"]),
            "joint_type": kind,
            "body0_group": first,
            "body1_group": second,
        }
        if first not in dynamic_groups and second not in dynamic_groups:
            record.update(
                {
                    "authored": False,
                    "reason": "Both groups are conservative static colliders",
                }
            )
            joint_records.append(record)
            continue
        if first not in body_paths or second not in body_paths:
            raise ValueError(
                f"Joint endpoints must resolve to geometric groups: {joint['joint_id']}"
            )
        path = physics_root + "/Joints/" + _name("Joint", str(joint["joint_id"]))
        schema = (
            UsdPhysics.RevoluteJoint
            if kind == "revolute"
            else UsdPhysics.PrismaticJoint
        )
        usd_joint = schema.Define(stage, path)
        usd_joint.CreateBody0Rel().SetTargets([Sdf.Path(body_paths[first])])
        usd_joint.CreateBody1Rel().SetTargets([Sdf.Path(body_paths[second])])
        usd_joint.CreateAxisAttr("Z")
        usd_joint.CreateCollisionEnabledAttr(config.joint_collision_enabled)
        defaults.append(
            {
                "joint_id": str(joint["joint_id"]),
                "field": "joint_collision_enabled",
                "value": config.joint_collision_enabled,
                "unit": "boolean",
                "source": "config.joint_collision_enabled",
                "reason": "SCA constraints do not specify collision filtering between their endpoint bodies",
            }
        )
        usd_joint.GetPrim().CreateAttribute(
            "simplecad:jointId", Sdf.ValueTypeNames.String
        ).Set(str(joint["joint_id"]))
        for side, group_id, suffix in (("a", first, "0"), ("b", second, "1")):
            connector = edge["connector_" + side]
            joint_world = np.asarray(
                by_id[str(connector["node_id"])]["world_matrix"]
            ) @ _frame_matrix(connector["local_frame"])
            local = np.linalg.inv(body_frames[group_id]) @ joint_world
            getattr(usd_joint, "CreateLocalPos" + suffix + "Attr")(
                Gf.Vec3f(*local[:3, 3])
            )
            getattr(usd_joint, "CreateLocalRot" + suffix + "Attr")(
                quaternion(local[:3, :3])
            )
        limit_key = "angle_limit" if kind == "revolute" else "distance_limit"
        limits = (joint.get("limits") or {}).get(limit_key)
        if limits is None:
            if config.joint_limit_policy == "error":
                raise ValueError(
                    f"Missing explicit limits for joint {joint['joint_id']!r}"
                )
            usd_joint.CreateLowerLimitAttr(-math.inf)
            usd_joint.CreateUpperLimitAttr(math.inf)
            defaults.append(
                {
                    "joint_id": str(joint["joint_id"]),
                    "field": "limits",
                    "value": "unbounded",
                    "unit": "degree" if kind == "revolute" else "m",
                    "source": "config.joint_limit_policy",
                    "reason": "SCA constraint does not provide explicit scalar limits",
                }
            )
            record["limits"] = None
        else:
            scale = 1.0 if kind == "revolute" else 0.001
            lower, upper = (
                float(limits["lower_value"]) * scale,
                float(limits["upper_value"]) * scale,
            )
            if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
                raise ValueError(f"Invalid limits for joint {joint['joint_id']!r}")
            usd_joint.CreateLowerLimitAttr(lower)
            usd_joint.CreateUpperLimitAttr(upper)
            record["limits"] = {
                "lower": lower,
                "upper": upper,
                "unit": "degree" if kind == "revolute" else "m",
            }
        record.update(
            {
                "authored": True,
                "prim_path": path,
                "axis": "connector local Z",
                "cad_initial_coordinate": dict(joint.get("parameters") or {}),
                "drive_authored": False,
            }
        )
        joint_records.append(record)

    return {
        "physics_root": physics_root,
        "body_count": len(dynamic_groups),
        "dynamic_body_count": len(dynamic_groups),
        "static_group_count": len(group_records) - len(dynamic_groups),
        "collider_count": collider_count,
        "joint_count": sum(item["authored"] for item in joint_records),
        "groups": group_records,
        "joints": joint_records,
        "fixed_constraints": fixed_records,
        "defaults": defaults,
        "unsupported": unsupported,
        "warnings": warnings,
        "mass_source": "SCA material or explicit default density times evaluated BREP volume",
        "inertia_source": "OCCT central BREP inertia, rigid rotation and parallel-axis aggregation",
        "component_layout": "Original semantic hierarchy retained and linked to visible physical geometry under sibling body groups",
        "simulation_verified": False,
    }


__all__ = ["apply_physics"]
