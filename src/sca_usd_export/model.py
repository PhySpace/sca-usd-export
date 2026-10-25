"""Read evaluated SCA occurrences and tessellate definitions in local mm."""

import hashlib
import re

import numpy as np


def stable_name(identifier):
    """USD-safe path segment; collisions cannot silently join CAD identifiers."""
    text = str(identifier)
    prefix = re.sub(r"[^A-Za-z0-9_]", "_", text)[:48]
    if not prefix or prefix[0].isdigit():
        prefix = "id_" + prefix
    return prefix + "_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def placement_matrix(placement):
    """Column-vector child-to-parent matrix in metres, with no axis rotation."""
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = np.asarray(
        [placement.x_axis, placement.y_axis, placement.z_axis]
    ).T
    matrix[:3, 3] = np.asarray(placement.origin) * 0.001
    if not np.isfinite(matrix).all():
        raise ValueError("SCA placement contains non-finite values")
    return matrix


def transformed_points(points, matrix):
    return np.asarray(points) @ matrix[:3, :3].T + matrix[:3, 3]


def validate_triangles(vertices, triangles):
    vertices, triangles = np.asarray(vertices), np.asarray(triangles)
    if vertices.ndim != 2 or vertices.shape[1:] != (3,) or not len(vertices):
        raise ValueError("mesh must contain Nx3 vertices")
    if not np.isfinite(vertices).all():
        raise ValueError("mesh coordinates must be finite")
    if triangles.ndim != 2 or triangles.shape[1:] != (3,) or not len(triangles):
        raise ValueError("mesh must contain Nx3 triangle indices")
    if not np.issubdtype(triangles.dtype, np.integer):
        raise ValueError("mesh indices must be integers")
    if triangles.min() < 0 or triangles.max() >= len(vertices):
        raise ValueError("mesh index out of range")
    points = vertices[triangles].astype(np.float64)
    cross = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    if not np.isfinite(cross).all() or np.any(np.linalg.norm(cross, axis=1) == 0):
        raise ValueError(
            "mesh contains degenerate triangles; no automatic repair is performed"
        )


def geometry_digest(points, triangles):
    digest = hashlib.sha256()
    digest.update(np.asarray(points, dtype="<f4").tobytes())
    digest.update(np.asarray(triangles, dtype="<i4").tobytes())
    return digest.hexdigest()


def collect_model(package, config):
    from simplecadapi.artifacts import materialize_definition
    from simplecadapi.exporter import tessellate_solid
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from simplecadapi.product.assembly import Assembly
    from simplecadapi.product.part import Part

    root = materialize_definition(package.root_definition)
    source_nodes = {n["node_id"]: n for n in package.occurrence_graph.nodes}
    records, meshes = [], {}

    def visit(item, node_id, parent_node_id, prim_path, local, parent_world, name):
        source = source_nodes[node_id]
        definition_id = source["definition_id"]
        world = parent_world @ local
        record = dict(
            node_id=node_id,
            parent_node_id=parent_node_id,
            definition_id=definition_id,
            kind=source["node_kind"],
            prim_path=prim_path,
            item=item,
            local_matrix=local,
            world_matrix=world,
            display_name=name or source["display_name"],
            source_node=dict(source),
        )
        records.append(record)
        if isinstance(item, Part):
            if definition_id not in meshes:
                vertices, triangles = tessellate_solid(
                    item.body.wrapped,
                    linear_deflection=config.linear_deflection_mm,
                    angular_deflection_degrees=config.angular_deflection_degrees,
                    relative=config.relative_deflection,
                )
                # The OpenCASCADE tessellator emits local CAD mm, not render-mesh m/Y.
                points = vertices * 0.001
                validate_triangles(points, triangles)
                stored = points.astype(np.float32)
                validate_triangles(stored, triangles)
                precision_error = float(np.max(np.abs(points - stored)))
                if precision_error > config.geometry_absolute_tolerance_m:
                    raise ValueError(
                        f"{definition_id}: USD float32 error {precision_error} m exceeds tolerance"
                    )
                volume_m3 = float(item.body.get_volume()) * 1e-9
                if not np.isfinite(volume_m3) or volume_m3 <= 0:
                    raise ValueError(
                        f"{definition_id}: BREP volume must be positive and finite"
                    )
                properties = GProp_GProps()
                # Explicit CGFlag/IFlag: the GK defaults compute volume only.
                BRepGProp.VolumePropertiesGK_s(
                    item.body.wrapped, properties, 1e-9, True, True, True, True
                )
                centre = properties.CentreOfMass()
                meshes[definition_id] = dict(
                    points=stored,
                    triangles=triangles,
                    source_points_m=points,
                    bounds_local_m=np.array([points.min(axis=0), points.max(axis=0)]),
                    volume_m3=volume_m3,
                    center_of_mass_local_m=np.asarray(
                        [centre.X(), centre.Y(), centre.Z()]
                    )
                    * 0.001,
                    definition_mesh_path=f"/Model/Definitions/{stable_name(definition_id)}/Mesh",
                    precision_error_m=precision_error,
                    geometry_sha256=geometry_digest(stored, triangles),
                )
            mesh = meshes[definition_id]
            record.update(
                {
                    key: mesh[key]
                    for key in (
                        "bounds_local_m",
                        "volume_m3",
                        "center_of_mass_local_m",
                        "definition_mesh_path",
                    )
                }
            )
            record["mesh_path"] = prim_path + "/Geometry/Mesh"
        elif isinstance(item, Assembly):
            for component in item.components:
                visit(
                    component.item,
                    node_id + "/" + component.component_id,
                    node_id,
                    prim_path + "/" + stable_name(component.component_id),
                    placement_matrix(component.placement),
                    world,
                    component.name,
                )
        else:
            raise ValueError(f"unsupported SCA item: {type(item).__name__}")

    visit(
        root,
        package.occurrence_graph.root_node_id,
        None,
        "/Model/Structure",
        np.eye(4),
        np.eye(4),
        root.name,
    )
    if {r["node_id"] for r in records} != set(source_nodes):
        raise ValueError("materialized tree does not match occurrence graph identities")
    return records, meshes
