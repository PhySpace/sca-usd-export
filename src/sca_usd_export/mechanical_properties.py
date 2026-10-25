"""The seven optional 1.1 mechanical parameters; no default injection.

PhysX API tokens and attribute types are taken from PhysxSchema 107.3.18.
The independent usd-core runtime need not load that native plugin to author
these real schema properties. Backend fallback use is verified separately.
"""

from copy import deepcopy


BODY = {
    "gravity_enabled": ("PhysxRigidBodyAPI", "physxRigidBody:disableGravity", "Bool", False),
    "linear_damping": ("PhysxRigidBodyAPI", "physxRigidBody:linearDamping", "Float", 0.0),
    "angular_damping": ("PhysxRigidBodyAPI", "physxRigidBody:angularDamping", "Float", 0.05),
}
COLLISION = {
    "contact_offset": ("PhysxCollisionAPI", "physxCollision:contactOffset", "Float", "-inf"),
    "rest_offset": ("PhysxCollisionAPI", "physxCollision:restOffset", "Float", "-inf"),
}
CONTACT = {
    "friction_combine_mode": ("PhysxMaterialAPI", "physxMaterial:frictionCombineMode", "Token", "average"),
    "restitution_combine_mode": ("PhysxMaterialAPI", "physxMaterial:restitutionCombineMode", "Token", "average"),
}


def applied_schema_tokens(prim):
    """GetAppliedSchemas filters unknown plugins; apiSchemas retains real tokens."""
    metadata = prim.GetMetadata("apiSchemas")
    return set(metadata.GetAppliedItems()) if metadata else set()


def merge_rigid_body(parts, settings, group_id):
    """A fixed group has one actor: equal requests merge; conflicts fail."""
    merged, sources = {}, {}
    for part in parts:
        node = part["node_id"]
        for field, parameter in settings["parts"][node].get("rigid_body", {}).items():
            previous = merged.get(field)
            signature = (parameter.get("mode", "explicit"), parameter.get("value"), parameter["unit"])
            if previous is not None and signature != (previous.get("mode", "explicit"), previous.get("value"), previous["unit"]):
                raise ValueError(f"Fixed group {group_id!r} has conflicting rigid_body.{field}: {sources[field]} versus {node}")
            merged.setdefault(field, deepcopy(parameter))
            sources.setdefault(field, []).append(node)
    return merged, sources


def author_optional(prim, parameters, specifications, *, scope, node_ids, body_mode=None):
    from pxr import Sdf

    records = []
    for field, (api, attribute_name, type_name, fallback) in specifications.items():
        parameter = parameters.get(field)
        record = {"scope": scope, "field": field, "node_ids": list(node_ids),
            "prim_path": str(prim.GetPath()), "api": api, "usd_attribute": attribute_name,
            "status": "omitted", "input": deepcopy(parameter), "usd_authored": False,
            "usd_type": str(getattr(Sdf.ValueTypeNames, type_name)),
            "usd_unit": "boolean" if field == "gravity_enabled" else "1/s" if field in {"linear_damping", "angular_damping"} else "m" if field in {"contact_offset", "rest_offset"} else "token",
            "schema_fallback": fallback, "fallback_target": "PhysxSchema 107.3.18",
            "backend_verified": False}
        if field in {"contact_offset", "rest_offset"}:
            record["fallback_note"] = ("-inf is a backend selection sentinel: contact offset depends on shape extent; rigid rest offset is selected as zero")
        if parameter is None:
            records.append(record)
            continue
        use_default = parameter.get("mode") == "engine_default"
        if body_mode == "static":
            if not use_default:
                raise ValueError("Static rigid group cannot use explicit rigid_body." + field)
            record["status"] = "not_applicable"
            record["reason"] = "Static collider has no rigid actor; engine_default creates no USD opinion"
            records.append(record)
            continue
        prim.AddAppliedSchema(api)
        if use_default:
            prim.RemoveProperty(attribute_name)
            attribute = prim.GetAttribute(attribute_name)
            if attribute and attribute.HasAuthoredValueOpinion():
                raise ValueError("Inherited authored value prevents engine_default for " + attribute_name)
            record["status"] = "engine_default"
        else:
            value = parameter["value"]
            if field == "gravity_enabled":
                value = not value
            attribute = prim.CreateAttribute(attribute_name, getattr(Sdf.ValueTypeNames, type_name), custom=False,
                variability=Sdf.VariabilityUniform if type_name == "Token" else Sdf.VariabilityVarying)
            if not attribute.Set(value):
                raise ValueError("Could not author " + attribute_name)
            record.update(status="authored", usd_authored=True, usd_value=attribute.Get())
        if body_mode == "kinematic":
            record["applicability"] = "no_dynamic_integration_in_kinematic_mode"
        records.append(record)
    return records


def validate_optional(stage, records):
    """Read back authored values/absence, never pretend a fallback was simulated."""
    import math

    for record in records:
        prim = stage.GetPrimAtPath(record["prim_path"])
        if not prim:
            raise ValueError("Optional physics target missing: " + record["prim_path"])
        attribute = prim.GetAttribute(record["usd_attribute"])
        authored = bool(attribute and attribute.HasAuthoredValueOpinion())
        if authored != record["usd_authored"]:
            raise ValueError("USD optional authored/default state changed: " + record["usd_attribute"])
        if record["status"] in {"authored", "engine_default"} and record["api"] not in applied_schema_tokens(prim):
            raise ValueError("USD optional physics API missing: " + record["api"])
        if authored:
            if str(attribute.GetTypeName()) != record["usd_type"] or attribute.IsCustom():
                raise ValueError("USD optional physics type changed: " + record["usd_attribute"])
            actual, expected = attribute.Get(), record["usd_value"]
            equal = (actual == expected if isinstance(expected, (str, bool)) else
                     actual is not None and math.isclose(actual, expected, rel_tol=2e-6, abs_tol=1e-12))
            if not equal:
                raise ValueError("USD optional physics value changed: " + record["usd_attribute"])
    return {"optional_properties_checked": len(records), "backend_defaults_verified": False}
