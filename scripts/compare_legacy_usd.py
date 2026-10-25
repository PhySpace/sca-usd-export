"""Read-only exact comparison of legacy USD physics, geometry and transforms.

Ignores custom reporting attributes/customData, but compares all composed real
schema physics attributes, their authored state, relationships, API tokens,
non-custom geometry attributes, and local/world transforms on every prim.
No exporter or CAD code is imported or run.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from pxr import Gf, Usd, UsdGeom, UsdPhysics

NEW_ATTRIBUTES = {
    "physxRigidBody:disableGravity", "physxRigidBody:linearDamping", "physxRigidBody:angularDamping",
    "physxCollision:contactOffset", "physxCollision:restOffset",
    "physxMaterial:frictionCombineMode", "physxMaterial:restitutionCombineMode",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (Gf.Quatd, Gf.Quatf, Gf.Quath)):
        return [canonical(float(value.GetReal())), canonical(value.GetImaginary())]
    try:
        return [canonical(item) for item in value]
    except TypeError:
        return str(value)


def attribute_snapshot(attribute):
    return {"type": str(attribute.GetTypeName()), "variability": str(attribute.GetVariability()),
        "authored": attribute.HasAuthoredValueOpinion(), "value": canonical(attribute.Get()),
        "time_samples": [[time, canonical(attribute.Get(time))] for time in attribute.GetTimeSamples()]}


def snapshot(path):
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise ValueError("Cannot open " + str(path))
    cache = UsdGeom.XformCache()
    result = {"meters_per_unit": UsdGeom.GetStageMetersPerUnit(stage),
        "kilograms_per_unit": UsdPhysics.GetStageKilogramsPerUnit(stage),
        "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
        "default_prim": str(stage.GetDefaultPrim().GetPath()), "prims": {}}
    counts = {"prims": 0, "physics_attributes": 0, "physics_relationships": 0,
              "geometry_attributes": 0, "xformable_prims": 0, "new_authored_properties": 0}
    for prim in stage.TraverseAll():
        api = prim.GetMetadata("apiSchemas")
        entry = {"type": prim.GetTypeName(), "specifier": str(prim.GetSpecifier()),
            "apis": list(api.GetAppliedItems()) if api else [], "physics": {}, "relationships": {}, "geometry": {}}
        for attribute in prim.GetAttributes():
            name = attribute.GetName()
            if name.startswith(("physics:", "physx", "drive:")):
                entry["physics"][name] = attribute_snapshot(attribute)
                counts["physics_attributes"] += 1
            elif not attribute.IsCustom():
                entry["geometry"][name] = attribute_snapshot(attribute)
                counts["geometry_attributes"] += 1
            if name in NEW_ATTRIBUTES and attribute.HasAuthoredValueOpinion():
                counts["new_authored_properties"] += 1
        for relationship in prim.GetRelationships():
            name = relationship.GetName()
            if name.startswith("physics:") or name == "material:binding:physics":
                entry["relationships"][name] = [str(target) for target in relationship.GetTargets()]
                counts["physics_relationships"] += 1
        xform = UsdGeom.Xformable(prim)
        if xform:
            entry["local_transform"] = canonical(xform.GetLocalTransformation())
            entry["world_transform"] = canonical(cache.GetLocalToWorldTransform(prim))
            entry["reset_xform_stack"] = xform.GetResetXformStack()
            counts["xformable_prims"] += 1
        result["prims"][str(prim.GetPath())] = entry
        counts["prims"] += 1
    return result, counts


def differences(first, second, path="", results=None):
    results = [] if results is None else results
    if isinstance(first, dict) and isinstance(second, dict):
        for key in sorted(first.keys() | second.keys()):
            location = path + "/" + key
            if key not in first or key not in second:
                results.append({"path": location, "old": first.get(key), "candidate": second.get(key)})
            else:
                differences(first[key], second[key], location, results)
    elif first != second:
        results.append({"path": path, "old": first, "candidate": second})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Choose a new report path")
    paths = [args.old.resolve(), args.candidate.resolve()]
    before = [sha(path) for path in paths]
    old, old_counts = snapshot(paths[0])
    candidate, counts = snapshot(paths[1])
    changes = differences(old, candidate)
    input_report = json.loads(paths[1].with_suffix(".report.json").read_text(encoding="utf-8"))
    resolved = input_report["physics"]["input_configuration"]["resolved"]
    declared = []
    for node, part in resolved["parts"].items():
        for section, fields in (("rigid_body", {"mode", "gravity_enabled", "linear_damping", "angular_damping"}),
                                ("collision", {"contact_offset", "rest_offset"}),
                                ("contact", {"friction_combine_mode", "restitution_combine_mode"})):
            declared.extend(node + "/" + section + "/" + field for field in fields & part.get(section, {}).keys())
    unchanged = before == [sha(path) for path in paths]
    result = {"ok": not changes and not declared and counts["new_authored_properties"] == 0 and unchanged,
        "old": str(paths[0]), "candidate": str(paths[1]), "source_sha256": before,
        "source_files_unchanged": unchanged, "comparison": "exact composed values and authored state; no numerical tolerance",
        "ignored": ["custom reporting attributes including sca:*/simplecad:*", "customData and report paths"],
        "old_counts": old_counts, "candidate_counts": counts, "differences": changes,
        "candidate_schema_version": resolved["schema_version"], "new_fields_declared": declared,
        "new_seven_physx_attributes_have_no_authored_opinions": counts["new_authored_properties"] == 0,
        "legacy_body_mode_opinions_unchanged": not any("rigidBodyEnabled" in change["path"] or "kinematicEnabled" in change["path"] for change in changes)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "differences": len(changes), "report": str(args.report)}))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
