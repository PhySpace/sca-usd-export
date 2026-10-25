"""Generate a conforming volume mesh for PhysX; contains no solver."""

from collections import Counter
import math
import numpy as np


def swept_tetrahedra(centerline, diameter, axial_segments, radial_segments):
    """Sweep a triangulated disk along a sampled polyline in SI world space.

    Each triangular prism is split by global vertex order into three tets. This
    gives matching shared-face diagonals, including the disk's closing sector.
    Endpoint rings are returned for explicit two-way vertex/xform attachments.
    """
    line = np.asarray(centerline, dtype=float)
    distances = np.linalg.norm(np.diff(line, axis=0), axis=1)
    cumulative = np.concatenate(([0.0], np.cumsum(distances)))
    positions = np.column_stack([np.interp(np.linspace(0, cumulative[-1], axial_segments + 1), cumulative, line[:, axis]) for axis in range(3)])
    tangent = np.empty_like(positions)
    tangent[0], tangent[-1] = positions[1] - positions[0], positions[-1] - positions[-2]
    tangent[1:-1] = positions[2:] - positions[:-2]
    norms = np.linalg.norm(tangent, axis=1)
    if np.any(norms < 1e-12):
        raise ValueError("Cable centreline contains a reversing or collapsed tangent")
    tangent /= norms[:, None]
    vertices, previous_normal = [], None
    for origin, direction in zip(positions, tangent):
        if previous_normal is None:
            reference = np.eye(3)[np.argmin(np.abs(direction))]
            normal = reference - direction * np.dot(direction, reference)
        else:
            normal = previous_normal - direction * np.dot(direction, previous_normal)
        if np.linalg.norm(normal) < 1e-8:
            raise ValueError("Cable curvature is too sharp for swept_tetrahedra")
        normal /= np.linalg.norm(normal)
        binormal = np.cross(direction, normal)
        previous_normal = normal
        vertices.append(origin)
        vertices.extend(origin + diameter / 2 * (math.cos(2 * math.pi * i / radial_segments) * normal + math.sin(2 * math.pi * i / radial_segments) * binormal) for i in range(radial_segments))
    points = np.asarray(vertices, dtype=np.float32)
    stride = radial_segments + 1
    tets = []
    for station in range(axial_segments):
        for sector in range(radial_segments):
            lower = sorted((station*stride, station*stride+sector+1, station*stride+(sector+1)%radial_segments+1))
            a, b, c = lower
            A, B, C = (v + stride for v in lower)
            for tet in ([a,b,c,C], [a,b,B,C], [a,A,B,C]):
                volume6 = np.linalg.det((points[np.asarray(tet[1:])] - points[tet[0]]).astype(float))
                if abs(volume6) < 1e-18:
                    raise ValueError("Cable discretization produced a degenerate tetrahedron; refine centreline/discretization")
                if volume6 < 0:
                    tet[1], tet[2] = tet[2], tet[1]
                tets.append(tet)
    counts, oriented = Counter(), {}
    for tet in tets:
        for opposite in range(4):
            face = [v for i, v in enumerate(tet) if i != opposite]
            a, b, c = points[face].astype(float)
            if np.dot(np.cross(b-a, c-a), points[tet[opposite]]-a) > 0:
                face[1], face[2] = face[2], face[1]
            key = tuple(sorted(face))
            counts[key] += 1
            oriented[key] = face
    if any(count > 2 for count in counts.values()):
        raise ValueError("Cable tetrahedra are non-manifold")
    surface = [oriented[key] for key, count in counts.items() if count == 1]
    ends = {"start": list(range(stride)), "end": list(range(axial_segments*stride, (axial_segments+1)*stride))}
    return points, np.asarray(tets, dtype=np.int32), np.asarray(surface, dtype=np.int32), ends
