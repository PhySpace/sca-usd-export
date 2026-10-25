"""Convert authored dimensional USD values; declaration/report data stays SI.

We convert local layer attribute specs exactly once, including definition class
meshes. Traversing composed references instead would double-convert inherited
geometry. No root scale or change in the actual physical size is introduced.
"""
import math
import numpy as np


def _factor(name, type_name, prim_type, length_factor, old_mpu, new_mpu):
    if name.startswith('sca:') or name.startswith('simplecad:'):
        return None
    if type_name in {'point3f', 'point3d', 'point3f[]', 'point3d[]'} or name == 'extent':
        return length_factor
    if name.startswith('xformOp:translate'):
        return length_factor
    if prim_type == 'Capsule' and name in {'radius', 'height'}:
        return length_factor
    if name == 'physics:velocity':
        return length_factor
    if name in {'physics:density', 'omniphysics:density'}:
        return (new_mpu / old_mpu) ** 3
    if name == 'omniphysics:youngsModulus':
        return new_mpu / old_mpu
    if name in {'physics:diagonalInertia'}:
        return length_factor ** 2
    if name in {'physics:gravityMagnitude', 'physxCollision:contactOffset', 'physxCollision:restOffset',
                'physxCollision:torsionalPatchRadius', 'physxCollision:minTorsionalPatchRadius',
                'physxDeformableBody:contactOffset', 'physxDeformableBody:restOffset'}:
        return length_factor
    if name in {'physxDeformableBody:sleepThreshold', 'physxDeformableBody:settlingThreshold'}:
        return length_factor
    if prim_type == 'PhysicsPrismaticJoint' and name in {'physics:lowerLimit', 'physics:upperLimit'}:
        return length_factor
    if prim_type == 'PhysicsPrismaticJoint' and name == 'physxJoint:maxJointVelocity':
        return length_factor
    if name in {'state:linear:physics:position', 'state:linear:physics:velocity'}:
        return length_factor
    if name in {'physxMimicJoint:rotX:gearing', 'physxMimicJoint:rotX:offset'}:
        # This exporter supports only angular reference -> linear follower.
        return length_factor
    if name in {'drive:linear:physics:targetPosition', 'drive:linear:physics:targetVelocity'}:
        return length_factor
    angular_drive = any(name.startswith('drive:' + axis + ':') for axis in ('angular', 'rotX', 'rotY', 'rotZ'))
    if angular_drive and name.endswith((':physics:stiffness', ':physics:damping')):
        return length_factor ** 2
    if name.endswith(':physics:maxForce'):
        return length_factor ** (2 if angular_drive else 1)
    return None


def convert_stage_units(stage, meters_per_unit):
    """Mutate only this stage's root-layer authored data and return an audit."""
    from pxr import Gf, UsdGeom
    target = float(meters_per_unit)
    old = float(UsdGeom.GetStageMetersPerUnit(stage))
    if not math.isfinite(target) or target <= 0:
        raise ValueError('meters_per_unit must be positive finite')
    if stage.GetRootLayer().subLayerPaths:
        raise ValueError('Dimensional conversion requires this exporter\'s single authored layer')
    factor = old / target
    changed = []
    layer = stage.GetRootLayer()

    def convert_value(value, name, type_name, prim_type):
        if value is None:
            return value, None
        # An unbounded scalar remains unbounded in every length unit. Do not
        # place Infinity in the JSON SI audit (strict JSON prohibits it).
        if isinstance(value, float) and math.isinf(value):
            return value, None
        if name.startswith('xformOp:transform') and type_name == 'matrix4d':
            array = np.asarray(value, dtype=float).copy()
            array[3, :3] *= factor
            return Gf.Matrix4d(array.tolist()), 'matrix_translation'
        scale = _factor(name, type_name, prim_type, factor, old, target)
        if scale is None:
            return value, None
        if type_name.endswith('[]'):
            return type(value)([item * scale for item in value]), scale
        return value * scale, scale

    def visit(prim):
        for attr in prim.attributes.values():
            value, scale = convert_value(attr.default, attr.name, str(attr.typeName), prim.typeName)
            if scale is not None:
                source = attr.default
                source = np.asarray(source).tolist() if not isinstance(source, (float, int)) else source
                attr.default = value
                changed.append({'path': str(attr.path), 'factor': scale, 'type': str(attr.typeName),
                                'source_value_si': source})
            for time in layer.ListTimeSamplesForPath(attr.path):
                value, scale = convert_value(layer.QueryTimeSample(attr.path, time), attr.name, str(attr.typeName), prim.typeName)
                if scale is not None:
                    layer.SetTimeSample(attr.path, time, value)
        for child in prim.nameChildren.values():
            visit(child)
    if old != target:
        for prim in layer.rootPrims:
            visit(prim)
    UsdGeom.SetStageMetersPerUnit(stage, target)
    return {'source_meters_per_unit': old, 'meters_per_unit': target,
            'length_value_factor': factor, 'mass_value_factor': 1.0,
            'declarations_and_report': 'SI metres, kilograms, pascals; JSON unchanged',
            'changed_authored_properties': changed,
            'method': 'single-layer dimensional value conversion; no xform scale; no geometry resizing'}


def stage_in_si(stage, *, unit_report=None, geometry_tolerance_m=1e-8):
    """Independent in-memory SI view for the existing physical validators."""
    from pxr import Sdf, Usd
    layer = Sdf.Layer.CreateAnonymous('sca-validation-si.usda')
    layer.TransferContent(stage.GetRootLayer())
    clone = Usd.Stage.Open(layer)
    convert_stage_units(clone, 1.0)
    if unit_report is not None:
        if unit_report.get('source_meters_per_unit') != 1.0:
            raise ValueError('Unit audit must originate from SI authoring')
        # SI float32 -> cm float32 -> SI is not byte-invertible. Check the
        # reopened physical values first, then use audited originals for the
        # existing exact hashes. This changes only the in-memory validation view.
        for record in unit_report['changed_authored_properties']:
            attr = clone.GetAttributeAtPath(record['path'])
            if not attr or str(attr.GetTypeName()) != record['type']:
                raise ValueError('Dimensional property missing/type changed: ' + record['path'])
            actual = np.asarray(attr.Get(), dtype=float)
            source = np.asarray(record['source_value_si'], dtype=float)
            coordinate = record['type'].startswith('point3') or record['path'].endswith('.extent') or record['factor'] == 'matrix_translation'
            if actual.shape != source.shape or not np.allclose(actual, source, rtol=0 if coordinate else 2e-6,
                    atol=geometry_tolerance_m if coordinate else 1e-12):
                raise ValueError('Dimensional conversion changed physical value: ' + record['path'])
            template = attr.Get()
            attr.Set(type(template)(record['source_value_si']))
    return clone
