"""Explicit ordinary-joint articulation tree policy for the pinned backend."""
from collections import defaultdict, deque


def author_articulation(stage, settings, joints, groups, group_of, root_node_id, physics_root):
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics
    request = settings.get("assembly", {}).get("articulation")
    if not request or not request["enabled"]["value"]:
        return {"enabled": False, "origin": "omitted compatibility" if not request else "explicit disabled"}
    selected = group_of(request["root_node_id"])
    fixed = request["fixed_base"]["value"]
    by_id = {g["group_id"]: g for g in groups}
    dynamic = {g["group_id"] for g in groups if g["mode"] == "dynamic"}
    if any(g["mode"] == "kinematic" for g in groups):
        raise ValueError("Explicit articulation does not accept kinematic links")
    if not dynamic:
        raise ValueError("Articulation requires dynamic geometric links")
    edges = [j for j in joints if j["authored"]]
    adjacency = defaultdict(list)
    for joint in edges:
        a, b = joint["body0_group"], joint["body1_group"]
        adjacency[a].append((b, joint))
        adjacency[b].append((a, joint))
    if selected not in by_id:
        raise ValueError("Articulation root must resolve to a geometric rigid group")
    world_edges = [j for j in edges if (j["body0_group"] in dynamic) != (j["body1_group"] in dynamic)]
    synthetic_fixed = None
    if fixed and selected not in dynamic:
        if len(world_edges) != 1 or selected not in {world_edges[0]["body0_group"], world_edges[0]["body1_group"]}:
            raise ValueError("Static-root articulation requires exactly one movable joint from selected grounded group to dynamic tree")
        root_joint = world_edges[0]
        root_path = root_joint["prim_path"]
        root_link = root_joint["body1_group"] if root_joint["body1_group"] in dynamic else root_joint["body0_group"]
        traversal_root = selected
    else:
        if selected not in dynamic or world_edges:
            raise ValueError("Floating or explicitly fixed dynamic articulation root requires an ungrounded connected dynamic tree")
        root_link, traversal_root = selected, selected
        if fixed:
            root_path = physics_root + "/Joints/ArticulationWorldFixed"
            joint = UsdPhysics.FixedJoint.Define(stage, root_path)
            joint.CreateBody1Rel().SetTargets([Sdf.Path(by_id[selected]["body_path"])])
            world = UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(by_id[selected]["body_path"]))
            position = world.ExtractTranslation()
            rotation = world.ExtractRotationQuat()
            joint.CreateLocalPos0Attr(Gf.Vec3f(position))
            joint.CreateLocalRot0Attr(Gf.Quatf(rotation))
            joint.CreateLocalPos1Attr(Gf.Vec3f(0))
            joint.CreateLocalRot1Attr(Gf.Quatf(1))
            synthetic_fixed = {"prim_path": root_path, "body1_path": by_id[selected]["body_path"],
                "world_position_m": list(position), "world_rotation_wxyz": [rotation.GetReal(), *rotation.GetImaginary()]}
        else:
            root_path = by_id[selected]["body_path"]
    visited, pending, tree = {traversal_root}, deque([traversal_root]), []
    used = set()
    while pending:
        parent = pending.popleft()
        for child, joint in adjacency[parent]:
            if joint["joint_id"] in used:
                continue
            used.add(joint["joint_id"])
            if child in visited:
                raise ValueError("Closed ordinary-joint loop cannot be included in this articulation; explicit loop exclusion is unsupported")
            if child not in dynamic:
                raise ValueError("Articulation contains an additional static/world endpoint")
            visited.add(child)
            pending.append(child)
            tree.append({"parent_group": parent, "child_group": child, "joint_id": joint["joint_id"],
                         "usd_body0_is_parent": joint["body0_group"] == parent})
    if visited & dynamic != dynamic or len(used) != len(edges):
        raise ValueError("Explicit articulation requires one connected ordinary-joint tree; disconnected links/joints remain")
    UsdPhysics.ArticulationRootAPI.Apply(stage.GetPrimAtPath(root_path))
    root_prim = stage.GetPrimAtPath(root_path)
    root_prim.AddAppliedSchema("PhysxArticulationAPI")
    self_collisions = request.get("self_collisions", {"value": True, "unit": "boolean",
        "source": {"kind": "default", "detail": "Exporter articulation policy preserves self contact; explicit collider-pair exclusions remain authoritative"}})
    root_prim.CreateAttribute("physxArticulation:enabledSelfCollisions", Sdf.ValueTypeNames.Bool, custom=False).Set(self_collisions["value"])
    return {"enabled": True, "configuration": request, "root_prim_path": root_path,
            "self_collisions": self_collisions,
            "fixed_base": fixed, "selected_group": selected, "root_link_group": root_link,
            "dynamic_body_paths": [by_id[g]["body_path"] for g in sorted(dynamic)],
            "joint_paths": [j["prim_path"] for j in edges], "topology": tree,
            "expected_link_count": len(dynamic), "expected_dof_count": len(edges),
            "synthetic_world_fixed": synthetic_fixed,
            "topology_policy": "native fixed groups merged; one acyclic connected ordinary-joint tree; static endpoint resolves to world",
            "runtime_verified": False}
