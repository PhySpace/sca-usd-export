"""Prepare the actual closed CAD occurrence surface for official volume cooking.

This module does no tetrahedralization. Welding joins OCCT face seam indices at
an explicit CAD precision tolerance; it neither moves points nor drops faces.
The returned SI world surface is the reproducible input to the Isaac asset build.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from .config import ExportConfig
from .model import collect_model, transformed_points, validate_triangles


def _weld_closed_surface(points, triangles, tolerance_m=1e-10):
    points = np.asarray(points, dtype=np.float64)
    triangles = np.asarray(triangles, dtype=np.int32)
    cells, unique, mapping = {}, [], []
    from itertools import product
    offsets = list(product((-1, 0, 1), repeat=3))
    for point in points:
        cell = tuple(np.floor(point / tolerance_m).astype(np.int64))
        found = None
        for offset in offsets:
            neighbor = tuple(a + b for a, b in zip(cell, offset))
            for index in cells.get(neighbor, ()):
                if np.linalg.norm(point - unique[index]) <= tolerance_m:
                    found = index
                    break
            if found is not None:
                break
        if found is None:
            found = len(unique)
            unique.append(point)
            cells.setdefault(cell, []).append(found)
        mapping.append(found)
    welded = np.asarray(unique, dtype=np.float64)
    faces = np.asarray(mapping, dtype=np.int32)[triangles]
    validate_triangles(welded, faces)
    undirected, directed = Counter(), Counter()
    seen = set()
    for face in faces:
        key = tuple(sorted(map(int, face)))
        if key in seen:
            raise ValueError("CAD source has duplicate surface faces; no automatic deletion is performed")
        seen.add(key)
        for a, b in zip(face, np.roll(face, -1)):
            undirected[tuple(sorted((int(a), int(b))))] += 1
            directed[(int(a), int(b))] += 1
    if any(count != 2 for count in undirected.values()):
        raise ValueError("Selected CAD occurrence tessellation is not a closed two-manifold surface")
    if any(directed[(a, b)] != directed[(b, a)] for a, b in directed):
        raise ValueError("Selected CAD occurrence has inconsistent surface winding")
    a, b, c = welded[faces].transpose(1, 0, 2)
    volume = float(np.einsum('ij,ij->i', a, np.cross(b, c)).sum() / 6)
    if volume <= 0:
        raise ValueError("Selected CAD surface has nonpositive oriented volume; no automatic reorientation is performed")
    return welded, faces, volume


def prepare_from_records(package, target_node_id, records, meshes, config):
    matches = [record for record in records if record['node_id'] == target_node_id]
    if len(matches) != 1 or matches[0]['kind'] != 'part':
        raise ValueError(f"Deformable target must identify exactly one existing CAD part occurrence: {target_node_id}")
    from .dependencies import require_physics
    require_physics()
    from sca_physics.deformable import geometry_source_snapshot
    record = matches[0]
    mesh = meshes[record['definition_id']]
    world = transformed_points(mesh['source_points_m'], record['world_matrix'])
    points, triangles, surface_volume = _weld_closed_surface(world, mesh['triangles'])
    tessellation = {name: getattr(config, name) for name in (
        'linear_deflection_mm', 'angular_deflection_degrees', 'relative_deflection')}
    tessellation['seam_weld_tolerance_m'] = 1e-10
    digest = hashlib.sha256()
    digest.update(np.asarray(points, dtype='<f8').tobytes())
    digest.update(np.asarray(triangles, dtype='<i4').tobytes())
    digest.update(json.dumps(tessellation, sort_keys=True, separators=(',', ':')).encode())
    return {
        'schema_version': '1.0', 'target_node_id': target_node_id,
        'geometry_source': geometry_source_snapshot(package, target_node_id),
        'source_geometry_sha256': digest.hexdigest(),
        'coordinate_frame': 'world', 'length_unit': 'm',
        'source_units': 'mm', 'source_to_si_length_scale': .001,
        'world_matrix_m': np.asarray(record['world_matrix']).tolist(),
        'points': points.tolist(), 'triangles': triangles.tolist(),
        'tessellation': tessellation,
        'bounds_world_m': [points.min(axis=0).tolist(), points.max(axis=0).tolist()],
        'cad_volume_m3': float(record['volume_m3']), 'surface_volume_m3': surface_volume,
        'closed_manifold': True,
        'preparation': 'Actual OCCT CAD tessellation; seam index welding only; no swept_tetrahedra or volume mesher',
    }


def prepare_deformable_surface(data, target_node_id, *, config=None):
    """Return a closed world-metre CAD surface, without requiring a declaration.

    A material edit does not affect this surface fingerprint. BREP, occurrence
    transform, units, tessellation, or source topology changes do affect it.
    """
    from simplecadapi.translator.package_units import read_product_package_translation_units
    config = config or ExportConfig()
    package, units = read_product_package_translation_units(data)
    if any(unit.definition.units != 'mm' for unit in units):
        raise ValueError('CAD deformable preparation requires canonical millimetre definitions')
    records, meshes = collect_model(package, config)
    return prepare_from_records(package, target_node_id, records, meshes, config)


def write_deformable_surface(data, target_node_id, output, *, config=None):
    surface = prepare_deformable_surface(data, target_node_id, config=config)
    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(surface, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')
    return {'output': str(destination), 'target_node_id': target_node_id,
            'source_geometry_sha256': surface['source_geometry_sha256'],
            'vertex_count': len(surface['points']), 'triangle_count': len(surface['triangles']),
            'closed_manifold': True, 'length_unit': 'm'}
