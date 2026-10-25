"""Native volume/rigid element filters for the cap and first axial span.

These are declarative collision masks. This module contains no solver or
per-frame logic. Indices refer to rows of TetMesh.surfaceFaceVertexIndices.
"""

from copy import deepcopy
import hashlib
import numpy as np

FILTER_ARCLENGTH_TOLERANCE_M = 1e-7


def endpoint_surface_indices(surface, radial_segments, axial_segments, endpoint):
    if axial_segments < 3 or endpoint not in {"start","end"}:
        raise ValueError("Endpoint collision filtering requires at least three axial segments and a start/end endpoint")
    stride = radial_segments+1
    triangles = np.asarray(surface,dtype=np.int64)
    mask = np.all(triangles < 2*stride,axis=1) if endpoint == "start" else np.all(triangles >= (axial_segments-1)*stride,axis=1)
    indices = np.flatnonzero(mask).tolist()
    if not indices or len(indices) >= len(triangles):
        raise ValueError("Endpoint element filter must select a nonempty proper subset of the cable surface")
    return indices


def physical_endpoint_surface_indices(points, surface, centerline, filter_length, endpoint):
    """Select actual surface faces by reference position and physical arc length.

    All vertices of a face must project within the declared endpoint interval.
    No vertex number, ring stride or volume-mesh subdivision count is assumed.
    """
    if endpoint not in {"start", "end"} or filter_length <= 0:
        raise ValueError("Physical endpoint filtering requires a start/end endpoint and positive length")
    points, centerline = np.asarray(points,dtype=float), np.asarray(centerline,dtype=float)
    starts, spans = centerline[:-1], np.diff(centerline,axis=0)
    lengths = np.linalg.norm(spans,axis=1)
    offsets = np.r_[0.,np.cumsum(lengths)]
    arc = []
    for point in points:
        fractions = np.clip(np.sum((point-starts)*spans,axis=1)/(lengths*lengths),0.,1.)
        distances = np.sum((starts+fractions[:,None]*spans-point)**2,axis=1)
        segment = int(np.argmin(distances))
        arc.append(offsets[segment]+fractions[segment]*lengths[segment])
    distance = np.asarray(arc) if endpoint == "start" else offsets[-1]-np.asarray(arc)
    triangles = np.asarray(surface,dtype=np.int64)
    indices = np.flatnonzero(np.all(distance[triangles] <= filter_length+FILTER_ARCLENGTH_TOLERANCE_M,axis=1)).tolist()
    if not indices or len(indices) >= len(triangles):
        raise ValueError("Physical endpoint element filter must select a nonempty proper subset")
    return indices


def author_endpoint_filters(stage, root_path, tet_path, group, endpoint, indices):
    from pxr import Sdf, UsdGeom, UsdPhysics
    scope = root_path+"/CollisionFilters"
    UsdGeom.Scope.Define(stage,scope)
    records=[]
    for part in group["parts"]:
        shape_path=part["shape_path"]
        shape=stage.GetPrimAtPath(shape_path)
        if not shape or not shape.HasAPI(UsdPhysics.CollisionAPI):
            raise ValueError("Endpoint filter target is not an actual rigid collider: "+shape_path)
        path=scope+"/Filter_"+endpoint+"_"+hashlib.sha256(shape_path.encode()).hexdigest()[:16]
        prim=stage.DefinePrim(path,"OmniPhysicsElementCollisionFilter")
        prim.CreateRelationship("omniphysics:src0",custom=False).SetTargets([Sdf.Path(tet_path)])
        prim.CreateRelationship("omniphysics:src1",custom=False).SetTargets([Sdf.Path(shape_path)])
        prim.CreateAttribute("omniphysics:filterEnabled",Sdf.ValueTypeNames.Bool,custom=False).Set(True)
        arrays={"groupElemCounts0":[len(indices)],"groupElemIndices0":indices,"groupElemCounts1":[],"groupElemIndices1":[]}
        for name,values in arrays.items():
            prim.CreateAttribute("omniphysics:"+name,Sdf.ValueTypeNames.UIntArray,custom=False).Set(values)
        records.append({"prim_path":path,"endpoint":endpoint,"src0":tet_path,"src1":shape_path,
            "rigid_body_path":group["body_path"],"rigid_node_id":part["node_id"],
            "filter_enabled":True,"surface_triangle_indices":list(indices),"arrays":deepcopy(arrays)})
    return records


def validate_endpoint_filters(stage, record, surface, physics_report):
    from pxr import UsdPhysics
    spec=record["declaration"]["simulation_mesh"]
    axial,radial=spec["axial_segments"]["value"],spec["radial_segments"]["value"]
    report_filters=record.get("element_collision_filters",[])
    if not physics_report:
        raise ValueError("Endpoint element filters require the rigid physics report for validation")
    groups={g["body_path"]:g for g in physics_report["groups"]}
    expected_pairs={}
    for attachment in record["attachments"]:
        endpoint=attachment["endpoint"]
        group=groups[attachment["rigid_body_path"]]
        if spec["method"] == "physx_conforming_tetrahedra":
            indices=physical_endpoint_surface_indices(spec["generated"]["reference_points"],surface,
                record["declaration"]["geometry"]["centerline"]["value"],spec["filter_length"]["value"],endpoint)
        else:
            indices=endpoint_surface_indices(surface,radial,axial,endpoint)
        for part in group["parts"]:
            expected_pairs[(endpoint,part["shape_path"])]=(indices,group["body_path"],part["node_id"])
    seen=set()
    for item in report_filters:
        key=(item["endpoint"],item["src1"])
        if key not in expected_pairs or key in seen:
            raise ValueError("Native endpoint filter has an unexpected/duplicate collider target")
        seen.add(key)
        indices,body_path,node_id=expected_pairs[key]
        if item["surface_triangle_indices"]!=indices or item["rigid_body_path"]!=body_path or item["rigid_node_id"]!=node_id:
            raise ValueError("Native endpoint filter report differs from actual surface topology/rigid group")
        prim=stage.GetPrimAtPath(item["prim_path"])
        if not prim or prim.GetTypeName()!="OmniPhysicsElementCollisionFilter":
            raise ValueError("Native endpoint element collision filter type changed")
        for name,target in (("src0",record["simulation_mesh_path"]),("src1",item["src1"])):
            if [str(p) for p in prim.GetRelationship("omniphysics:"+name).GetTargets()]!=[target]:
                raise ValueError("Native endpoint element collision filter target changed")
        shape=stage.GetPrimAtPath(item["src1"])
        if not shape or not shape.HasAPI(UsdPhysics.CollisionAPI) or not str(shape.GetPath()).startswith(body_path+"/"):
            raise ValueError("Native endpoint element filter target is not a collider in its attached rigid group")
        enabled=prim.GetAttribute("omniphysics:filterEnabled")
        if not enabled or str(enabled.GetTypeName())!="bool" or enabled.Get() is not True or enabled.IsCustom() or not enabled.HasAuthoredValueOpinion():
            raise ValueError("Native endpoint element filter enable state changed")
        arrays={"groupElemCounts0":[len(indices)],"groupElemIndices0":indices,"groupElemCounts1":[],"groupElemIndices1":[]}
        if item["arrays"]!=arrays or not item["filter_enabled"] or item["src0"]!=record["simulation_mesh_path"]:
            raise ValueError("Native endpoint element filter report arrays changed")
        for name,values in arrays.items():
            attr=prim.GetAttribute("omniphysics:"+name)
            if not attr or str(attr.GetTypeName())!="uint[]" or attr.IsCustom() or not attr.HasAuthoredValueOpinion() or list(attr.Get())!=values:
                raise ValueError("Native endpoint element collision filter indices/counts changed: "+name)
    if seen!=set(expected_pairs):
        raise ValueError("Native endpoint filters do not cover every actual attached rigid collider")
    actual={str(p.GetPath()) for p in stage.Traverse() if p.GetTypeName()=="OmniPhysicsElementCollisionFilter" and str(p.GetPath()).startswith(record["prim_path"]+"/")}
    if actual!={item["prim_path"] for item in report_filters}:
        raise ValueError("Native endpoint filter prim set changed")
