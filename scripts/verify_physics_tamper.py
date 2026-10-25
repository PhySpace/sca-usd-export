"""Persist deliberate USD corruption and prove the validator rejects each case.

The valid source and its report remain unchanged. Run in the add-on's isolated
environment after a successful export. Output directories/files must be new.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

from sca_usd_export.validation import validate_usd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    source, destination = args.source.resolve(), args.destination.resolve()
    if args.report.exists() or destination.exists():
        raise FileExistsError("tamper outputs already exist; choose new paths")
    expected = json.loads(source.with_suffix(".report.json").read_text(encoding="utf-8"))
    validate_usd(source, expected=expected)
    destination.mkdir(parents=True)
    material_part = next(group for group in expected["physics"]["groups"] if group["dynamic"])["parts"][0]
    joint_record = next(j for j in expected["physics"]["joints"] if j["authored"])
    cases = ["friction", "restitution", "damping", "collision_enabled", "collision_approximation",
             "up_axis", "body_relationship", "anchor", "axis", "joint_filter"]
    results = []
    for case in cases:
        output = destination / (case + ".usda")
        shutil.copyfile(source, output)
        stage = Usd.Stage.Open(str(output))
        # Keep each copied stage's history asset valid at its new location.
        history = source.with_suffix(".history") / "history.json"
        stage.GetDefaultPrim().GetAttribute("sca:history").Set(Sdf.AssetPath(str(history)))
        joint = UsdPhysics.RevoluteJoint.Get(stage, joint_record["prim_path"])
        shape = stage.GetPrimAtPath(material_part["shape_path"])
        if case == "friction":
            attribute = UsdPhysics.MaterialAPI(stage.GetPrimAtPath(material_part["contact_material_path"])).GetStaticFrictionAttr()
            replacement = float(attribute.Get()) + 0.2
        elif case == "restitution":
            attribute = UsdPhysics.MaterialAPI(stage.GetPrimAtPath(material_part["contact_material_path"])).GetRestitutionAttr()
            replacement = 0.9 if attribute.Get() != 0.9 else 0.1
        elif case == "damping":
            attribute = UsdPhysics.DriveAPI(joint.GetPrim(), "angular").GetDampingAttr()
            replacement = float(attribute.Get()) * 3 + 0.001
        elif case == "collision_enabled":
            attribute = UsdPhysics.CollisionAPI(shape).GetCollisionEnabledAttr()
            replacement = not attribute.Get()
        elif case == "collision_approximation":
            attribute = UsdPhysics.MeshCollisionAPI(shape).GetApproximationAttr()
            replacement = "boundingCube" if attribute.Get() != "boundingCube" else "convexHull"
        elif case == "up_axis":
            attribute = None
            before = UsdGeom.GetStageUpAxis(stage)
            replacement = "Y" if before == "Z" else "Z"
            UsdGeom.SetStageUpAxis(stage, replacement)
        elif case == "body_relationship":
            attribute = None
            relation = joint.GetBody1Rel()
            before = [str(p) for p in relation.GetTargets()]
            replacement = [joint_record["body0_path"]]
            relation.SetTargets([Sdf.Path(p) for p in replacement])
        elif case == "anchor":
            attribute = joint.GetLocalPos0Attr()
            replacement = attribute.Get() + Gf.Vec3f(0.125, 0, 0)
        elif case == "joint_filter":
            attribute = None
            pair = next(p for p in expected["physics"]["collision_exclusions"]
                        if any(r["origin"] == "joint.collision_enabled=false" for r in p["reasons"]))
            relation = stage.GetPrimAtPath(pair["a_path"]).GetRelationship("physics:filteredPairs")
            before = [str(p) for p in relation.GetTargets()]
            replacement = []
            relation.ClearTargets(True)
        else:
            attribute = joint.GetAxisAttr()
            replacement = "X"
        if attribute:
            before = attribute.Get()
            attribute.Set(replacement)
            field = str(attribute.GetPath())
        else:
            field = "stage.upAxis" if case == "up_axis" else str(relation.GetPath())
        stage.GetRootLayer().Save()
        try:
            validate_usd(output, expected=expected)
        except ValueError as exc:
            results.append({"case": case, "file": str(output), "field": field,
                            "before": str(before), "after": str(replacement),
                            "rejected": True, "reason": str(exc)})
        else:
            raise AssertionError("validator missed deliberately damaged " + case)
    result = {"ok": True, "valid_source": str(source),
              "valid_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "expected_report": str(source.with_suffix(".report.json")),
              "detected_case_count": len(results), "cases": results}
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
