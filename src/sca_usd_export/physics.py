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
    stage: Any, records: list[dict[str, Any]], occurrence_graph: Any, config: Any,
    settings: Any = None,
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
    from .embedded_physics import legacy_settings
    from .mechanical_properties import BODY, COLLISION, CONTACT, author_optional, merge_rigid_body

    settings = settings or legacy_settings(records, occurrence_graph, config)

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
    deformable_targets = {
        cable['target_node_id']: cable_id
        for cable_id, cable in settings.get('cables', {}).items()
        if cable.get('target_node_id') is not None
    }
    for target, cable_id in deformable_targets.items():
        if target not in by_id or by_id[target]['kind'] != 'part':
            raise ValueError(f"{cable_id}: cable replacement target must resolve to an existing CAD part occurrence: {target}")
        if settings['parts'][target].get('rigid_body'):
            raise ValueError(f"{target}: rigid_body declaration conflicts with a cable replacement; explicitly convert it through sca-physics")
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
        if first in deformable_targets or second in deformable_targets:
            raise ValueError(f"Deformable CAD occurrence is natively grounded: {first} -> {second}; remove grounding and declare supported explicit endpoint attachments")
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
        if edge['a'] in deformable_targets or edge['b'] in deformable_targets:
            raise ValueError(f"CAD joint {joint['joint_id']} references selected deformable occurrence; rigid CAD joint semantics cannot be silently discarded; use explicit deformable endpoint attachments")
        kind = str(joint["joint_type"])
        if kind == "fixed":
            if settings["joints"][str(joint["joint_id"] )]["collision_enabled"]["value"]:
                raise ValueError(f"Fixed joint {joint['joint_id']}: collision_enabled=true is unsupported inside one rigid group")
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
        group_id: [by_id[node] for node in nodes if by_id[node]["kind"] == "part" and node not in deformable_targets]
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
    group_modes, rigid_settings, rigid_sources = {}, {}, {}
    for group_id, parts in part_groups.items():
        if not parts:
            continue
        rigid, contributors = merge_rigid_body(parts, settings, group_id)
        mode = rigid.get("mode", {}).get("value", "dynamic" if group_id in dynamic_groups else "static")
        if mode not in {"static", "dynamic", "kinematic"}:
            raise ValueError(f"Invalid rigid body mode for group {group_id!r}: {mode!r}")
        if mode != "static" and (group_id in frozen or (group_id == root_group and root_is_assembly)):
            raise ValueError(f"Group {group_id!r} is natively grounded or frozen; cannot request {mode}")
        if mode == "static" and any(field != "mode" and parameter.get("mode") != "engine_default" for field, parameter in rigid.items()):
            raise ValueError(f"Static group {group_id!r} cannot use explicit gravity/damping")
        group_modes[group_id], rigid_settings[group_id], rigid_sources[group_id] = mode, rigid, contributors
    dynamic_groups = {group_id for group_id, mode in group_modes.items() if mode == "dynamic"}
    kinematic_groups = {group_id for group_id, mode in group_modes.items() if mode == "kinematic"}
    articulation_request = settings.get("assembly", {}).get("articulation")
    articulation_enabled = bool(articulation_request and articulation_request["enabled"]["value"])
    anchored_group = None
    if articulation_enabled and articulation_request["fixed_base"]["value"]:
        selected = groups.find(articulation_request["root_node_id"])
        if selected not in group_modes:
            raise ValueError("Fixed articulation root must resolve to a geometric rigid group")
        if group_modes[selected] == "static":
            # Grounding remains exact, represented by a world FixedJoint. PhysX
            # needs a rigid root link, not a static collider as an inbound link.
            if selected in frozen:
                raise ValueError("Articulation root cannot be frozen by unsupported CAD constraints")
            anchored_group = selected
            group_modes[selected] = "dynamic"
            dynamic_groups.add(selected)
    actor_groups = dynamic_groups | kinematic_groups
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
    UsdGeom.Scope.Define(stage, physics_root + "/ContactMaterials")

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
    shape_paths = {}
    optional_records = []
    for group_id in sorted(part_groups):
        parts = part_groups[group_id]
        if not parts:
            continue
        dynamic = group_id in dynamic_groups
        mode = group_modes[group_id]
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
        if group_id in actor_groups:
            rigid_api = UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
            rigid_api.CreateRigidBodyEnabledAttr(True)
            rigid_api.CreateKinematicEnabledAttr(mode == "kinematic")
        optional_records.extend(author_optional(body.GetPrim(), rigid_settings[group_id], BODY,
            scope="rigid_body", node_ids=[part["node_id"] for part in parts], body_mode=mode))
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
        if "mode" in rigid_settings[group_id]:
            reason = "explicit_package_body_mode"
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
            part_settings = settings["parts"][record["node_id"]]
            contact = part_settings["contact"]
            collision = part_settings["collision"]
            enabled = collision["enabled"]["value"]
            approximation = collision["approximation_dynamic" if group_id in actor_groups else "approximation_static"]["value"]
            if group_id in actor_groups and enabled and approximation == "none":
                raise ValueError(f"{record['node_id']}: dynamic triangle mesh approximation 'none' is unsupported; choose convexHull")
            if approximation not in allowed_approximations:
                raise ValueError(f"{record['node_id']}: unsupported collision approximation {approximation!r}")
            if dynamic and enabled and approximation == "convexHull":
                warnings.append(f"{record['node_id']}: convexHull collision can fill holes and concavities; simulator collision cooking remains unverified")
            UsdPhysics.CollisionAPI.Apply(shape).CreateCollisionEnabledAttr(enabled)
            UsdPhysics.MeshCollisionAPI.Apply(shape).CreateApproximationAttr(
                approximation
            )
            optional_records.extend(author_optional(shape, collision, COLLISION,
                scope="collision", node_ids=[record["node_id"]]))
            material_path = physics_root + "/ContactMaterials/" + _name("Contact", record["node_id"])
            material = UsdShade.Material.Define(stage, material_path)
            material_api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
            material_api.CreateStaticFrictionAttr(contact["static_friction"]["value"])
            material_api.CreateDynamicFrictionAttr(contact["dynamic_friction"]["value"])
            material_api.CreateRestitutionAttr(contact["restitution"]["value"])
            UsdShade.MaterialBindingAPI.Apply(shape).Bind(
                material, materialPurpose="physics"
            )
            optional_records.extend(author_optional(material.GetPrim(), contact, CONTACT,
                scope="contact", node_ids=[record["node_id"]]))
            density, density_source = _density(record["item"], config)
            for field, parameter in contact.items():
                if field in CONTACT:
                    continue
                defaults.append(
                    {
                        "node_id": record["node_id"],
                        "field": field,
                        "value": parameter["value"],
                        "unit": parameter["unit"],
                        "source": parameter["source"],
                        "reason": parameter["source"]["detail"],
                    }
                )
            defaults.append(
                {
                    "node_id": record["node_id"],
                    "field": "collision_approximation",
                    "value": approximation,
                    "unit": "policy",
                    "source": collision["approximation_dynamic" if group_id in actor_groups else "approximation_static"]["source"],
                    "reason": "Resolved sca_physics collision policy; none means triangle mesh, not disabled collision",
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
                    "contact_material_path": material_path,
                    "contact": contact,
                    "collision_enabled": enabled,
                    "collision_approximation": approximation,
                    "collision": collision,
                    "_com": com,
                    "_inertia": inertia,
                }
            )
            mass_sum += mass
            com_sum += mass * com
            collider_count += 1
            shape_paths[record["node_id"]] = shape_path
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
                "mode": mode,
                "kinematic": mode == "kinematic",
                "anchored_root": group_id == anchored_group,
                "rigid_body": rigid_settings[group_id],
                "rigid_body_sources": rigid_sources[group_id],
                "reason": reason,
                "collision_approximations": sorted({part["collision_approximation"] for part in part_properties}),
                "mass_kg": mass_sum,
                "center_of_mass_body_m": com.tolist(),
                "inertia_tensor_body_kg_m2": tensor.tolist(),
                "principal_inertia_kg_m2": moments.tolist(),
                "parts": part_properties,
            }
        )

    joint_records: list[dict[str, Any]] = []
    mimic_followers = {c["follower_joint_id"] for c in settings.get("couplings", {}).values()}
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
        if first not in actor_groups and second not in actor_groups:
            record.update(
                {
                    "authored": False,
                    "reason": "Both groups are conservative static colliders",
                    "requested_configuration": settings["joints"][str(joint["joint_id"])],
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
        joint_settings = settings["joints"][str(joint["joint_id"]) ]
        usd_joint.CreateCollisionEnabledAttr(joint_settings["collision_enabled"]["value"])
        defaults.append(
            {
                "joint_id": str(joint["joint_id"]),
                "field": "joint_collision_enabled",
                "value": joint_settings["collision_enabled"]["value"],
                "unit": "boolean",
                "source": joint_settings["collision_enabled"]["source"],
                "reason": joint_settings["collision_enabled"]["source"]["detail"],
            }
        )
        usd_joint.GetPrim().CreateAttribute(
            "simplecad:jointId", Sdf.ValueTypeNames.String
        ).Set(str(joint["joint_id"]))
        frames = {}
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
            frames[suffix] = {"position_m": local[:3, 3].tolist(),
                              "rotation_matrix": local[:3, :3].tolist(),
                              "world_position_m": joint_world[:3, 3].tolist(),
                              "world_axis": joint_world[:3, 2].tolist()}
        # The SI addon declaration is authoritative when motion is explicit.
        # Otherwise preserve the native CAD limit/legacy-unbounded policy.
        limit_key = "angle_limit" if kind == "revolute" else "distance_limit"
        limits = (joint.get("limits") or {}).get(limit_key)
        motion = joint_settings.get("motion", {}).get("value")
        if motion == "free":
            limits = None
        elif motion == "limited":
            scale = 180 / math.pi if kind == "revolute" else 1000
            limits = {"lower_value": joint_settings["lower_limit"]["value"] * scale,
                      "upper_value": joint_settings["upper_limit"]["value"] * scale}
        if limits is None:
            if config.joint_limit_policy == "error" and motion != "free":
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
        from .joint_drives import author_drive, author_joint_extensions
        damping = joint_settings["damping"]
        follower = str(joint["joint_id"]) in mimic_followers
        effective_drive = (None if follower else author_drive(usd_joint.GetPrim(), kind, joint_settings))
        # J = inv(G0*L0)*(G1*L1), matching USD's joint-frame definition.
        first_world = np.eye(4)
        second_world = np.eye(4)
        for matrix, frame in ((first_world, frames["0"]), (second_world, frames["1"])):
            matrix[:3, :3] = body_frames[first if matrix is first_world else second][:3, :3] @ np.array(frame["rotation_matrix"])
            matrix[:3, 3] = frame["world_position_m"]
        relative = np.linalg.inv(first_world) @ second_world
        coordinate = math.atan2(relative[1, 0], relative[0, 0]) if kind == "revolute" else float(relative[2, 3])
        native_parameter = "drive_angle_degrees" if kind == "revolute" else "drive_distance"
        parameters = joint.get("parameters") or {}
        if native_parameter in parameters:
            native_coordinate = float(parameters[native_parameter]) * (math.pi / 180 if kind == "revolute" else .001)
            error = coordinate - native_coordinate
            if kind == "revolute":
                error = math.remainder(error, 2 * math.pi)
            if abs(error) > 2e-6:
                raise ValueError(f"Solved connector coordinate disagrees with native CAD initial parameter: {joint['joint_id']}")
            # The solved rotation matrix alone loses whole revolutions. Keep
            # the native parameter after checking physical frame consistency.
            coordinate = native_coordinate
        extensions = author_joint_extensions(usd_joint.GetPrim(), kind, joint_settings, coordinate, articulation_enabled)
        record.update(
            {
                "authored": True,
                "prim_path": path,
                "axis": "connector local Z",
                "cad_initial_coordinate": dict(joint.get("parameters") or {}),
                "drive_authored": not follower,
                "body0_path": body_paths[first], "body1_path": body_paths[second],
                "frames": frames,
                "collision_enabled": joint_settings["collision_enabled"],
                "damping": damping,
                "joint_configuration": joint_settings,
                "motion": motion or "native_or_legacy",
                "effective_drive": effective_drive,
                "physx_extensions": extensions,
                "initial_coordinate_si": coordinate,
                "passive_drive": {"name": "angular" if kind == "revolute" else "linear", "type": "force", "stiffness": 0.0,
                    "target_position": 0.0, "target_velocity": 0.0,
                    "damping": damping["value"] * (math.pi / 180 if kind == "revolute" else 1.0),
                    "unit": "kg*m^2/(s*degree)" if kind == "revolute" else "kg/s",
                    "equation": "torque=-c*angular_velocity" if kind == "revolute" else "force=-c*linear_velocity",
                    "conversion": "SI N*m*s/rad times pi/180" if kind == "revolute" else "SI N*s/m unchanged"},
            }
        )
        joint_records.append(record)

    from .articulation import author_articulation
    articulation = author_articulation(stage, settings, joint_records, group_records,
        groups.find, str(occurrence_graph.root_node_id), physics_root)
    from .joint_drives import author_mimic_couplings
    couplings = author_mimic_couplings(stage, settings, joint_records, articulation)

    filter_pairs = {}

    def add_filter(first_id, second_id, reason):
        if first_id not in shape_paths or second_id not in shape_paths:
            raise ValueError("Collision exclusion must reference two existing part occurrences")
        first_id, second_id = sorted((first_id, second_id))
        key = (first_id, second_id)
        pair = filter_pairs.setdefault(key, {"a": first_id, "b": second_id,
            "a_path": shape_paths[first_id], "b_path": shape_paths[second_id], "reasons": []})
        pair["reasons"].append(reason)

    for pair in settings["assembly"]["collision_exclusions"]:
        if pair['a'] in deformable_targets or pair['b'] in deformable_targets:
            raise ValueError('Rigid assembly collision exclusion references a deformable target; use its explicit deformable attachment filtering policy')
        add_filter(pair["a"], pair["b"], {"origin": "assembly.collision_exclusions", "source": pair["source"]})
    for edge in joint_edges:
        joint_id = str(edge["joint"]["joint_id"])
        collision_parameter = settings["joints"][joint_id]["collision_enabled"]
        first, second = groups.find(edge["a"]), groups.find(edge["b"])
        if collision_parameter["value"] is False and first != second:
            # PhysX resolves a static collider joint endpoint to world. In that
            # case joint collisionEnabled=false alone does not filter its
            # shapes; explicit collider pairs enforce the package policy.
            for first_part in part_groups[first]:
                for second_part in part_groups[second]:
                    add_filter(first_part["node_id"], second_part["node_id"], {
                        "origin": "joint.collision_enabled=false", "joint_id": joint_id,
                        "source": collision_parameter["source"]})
    exclusions = list(filter_pairs.values())
    for edge in joint_edges:
        joint_id = str(edge["joint"]["joint_id"])
        if settings["joints"][joint_id]["collision_enabled"]["value"]:
            first, second = groups.find(edge["a"]), groups.find(edge["b"])
            for first_part in part_groups[first]:
                for second_part in part_groups[second]:
                    pair = filter_pairs.get(tuple(sorted((first_part["node_id"], second_part["node_id"]))))
                    if pair:
                        warnings.append(f"{joint_id}: collision_enabled=true is overridden by an explicit or another joint-derived collider exclusion for {pair['a']} and {pair['b']}")
    for pair in exclusions:
        UsdPhysics.FilteredPairsAPI.Apply(stage.GetPrimAtPath(pair["a_path"])).CreateFilteredPairsRel().AddTarget(Sdf.Path(pair["b_path"]))

    return {
        "physics_root": physics_root,
        "deformable_targets": [{'node_id': target, 'cable_id': cable_id,
            'physical_type': 'deformable_volume',
            'rigid_routing': 'Excluded before rigid grouping, BREP mass integration and rigid collider authoring',
            'mass_precedence': 'Explicit deformable material density times native FEM volume; CAD material density and rigid mass are not authored on this target'}
            for target, cable_id in sorted(deformable_targets.items())
            if settings['cables'][cable_id].get('representation', 'physx_deformable_volume') == 'physx_deformable_volume'],
        "rigid_chain_targets": [{'node_id': target, 'chain_id': cable_id,
            'physical_type': 'rigid_joint_chain',
            'rigid_routing': 'Excluded from CAD mass, collider and rigid grouping before capsule chain authoring',
            'mass_precedence': 'Explicit linear density times material arc length; no duplicate CAD rigid mass'}
            for target, cable_id in sorted(deformable_targets.items())
            if settings['cables'][cable_id].get('representation') == 'rigid_joint_chain'],
        "body_count": len(actor_groups),
        "dynamic_body_count": len(dynamic_groups),
        "anchored_root_body_count": int(anchored_group is not None),
        "movable_body_count": len(dynamic_groups) - int(anchored_group is not None),
        "articulation": articulation,
        "couplings": couplings,
        "kinematic_body_count": len(kinematic_groups),
        "static_group_count": len(group_records) - len(actor_groups),
        "optional_properties": optional_records,
        "collider_count": collider_count,
        "joint_count": sum(item["authored"] for item in joint_records) + int(bool(articulation.get("synthetic_world_fixed"))),
        "ordinary_joint_count": sum(item["authored"] for item in joint_records),
        "world_fixed_joint_count": int(bool(articulation.get("synthetic_world_fixed"))),
        "groups": group_records,
        "joints": joint_records,
        "fixed_constraints": fixed_records,
        "defaults": defaults,
        "unsupported": unsupported,
        "warnings": warnings,
        "collision_exclusions": exclusions,
        "scene": {"prim_path": str(scene.GetPath()), "up_axis": up_axis,
                  "gravity_direction": [0, -1, 0] if up_axis == "Y" else [0, 0, -1],
                  "gravity_m_s2": gravity},
        "mass_source": "SCA material or explicit default density times evaluated BREP volume",
        "inertia_source": "OCCT central BREP inertia, rigid rotation and parallel-axis aggregation",
        "component_layout": "Original semantic hierarchy retained and linked to visible physical geometry under sibling body groups",
        "simulation_verified": False,
    }


__all__ = ["apply_physics"]
