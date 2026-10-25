"""Optional bounded native PhysX smoke check; run with the user's Isaac Python.

Reads an exported asset and report only. It never constructs a chain, alters
material/solver values, writes the source layer or certifies physical accuracy.
"""
import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback

import numpy as np


LIMITS = {"joint_gap_m": .0005, "quaternion_norm_error": 1e-4,
          "mass_relative_error": 1e-4, "inertia_relative_error": 1e-4,
          "speed_m_s": 10.0}
RESULT_SCHEMA = "cable-runtime-task-1"


class ConfigurationMismatch(RuntimeError):
    """Saved description and the consumed asset/native configuration disagree."""


class EngineeringGuardStop(RuntimeError):
    """A legacy engineering guard requested a stop, not a solver exception."""


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _rotate(quaternions, vectors):
    quaternions = quaternions / np.linalg.norm(quaternions, axis=-1, keepdims=True)
    cross = 2 * np.cross(quaternions[..., :3], vectors)
    return vectors + quaternions[..., 3, None] * cross + np.cross(quaternions[..., :3], cross)


def load_task_criteria(path):
    if path is None:
        return None
    criteria = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = {"schema_version", "task_id", "basis", "required_duration_s", "max_joint_gap_m",
               "max_endpoint_gap_m", "max_free_extension_fraction", "tip_bending"}
    if not isinstance(criteria, dict) or set(criteria) - allowed:
        raise ValueError("Task criteria must be an object containing only documented fields")
    if criteria.get("schema_version") != "1.0":
        raise ValueError("Task criteria schema_version must be 1.0")
    for key in ("task_id", "basis"):
        if not isinstance(criteria.get(key), str) or not criteria[key].strip():
            raise ValueError("Task criteria require nonempty " + key)

    def number(obj, key, *, positive=False):
        item = obj[key]
        if type(item) not in (int, float) or not math.isfinite(item) or item < 0 or (positive and item == 0):
            raise ValueError("Task criterion must be a finite " + ("positive " if positive else "nonnegative ") + key)

    if "required_duration_s" not in criteria:
        raise ValueError("Task criteria require required_duration_s")
    number(criteria, "required_duration_s", positive=True)
    for key in allowed - {"schema_version", "task_id", "basis", "required_duration_s", "tip_bending"}:
        if key in criteria:
            number(criteria, key)
    if "tip_bending" in criteria:
        bending = criteria["tip_bending"]
        keys = {"axis", "direction", "window_s", "minimum_mean_displacement_m"}
        if not isinstance(bending, dict) or set(bending) != keys:
            raise ValueError("tip_bending requires exactly axis, direction, window_s and minimum_mean_displacement_m")
        if bending["axis"] not in ("X", "Y", "Z") or type(bending["direction"]) is not int or bending["direction"] not in (-1, 1):
            raise ValueError("tip_bending axis must be X/Y/Z and direction must be -1 or 1")
        number(bending, "window_s", positive=True)
        number(bending, "minimum_mean_displacement_m")
        if bending["window_s"] > criteria["required_duration_s"]:
            raise ValueError("tip_bending window cannot exceed the required duration")
    return criteria


def create_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "report", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--chain-id", required=True)
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--duration", type=float, help="seconds; default .25")
    duration.add_argument("--steps", type=int, help="exact steps, including initialization")
    parser.add_argument("--max-duration", type=float, default=.5)
    parser.add_argument("--max-steps", type=int, default=2048)
    parser.add_argument("--mode", choices=("legacy", "task"), default="legacy")
    parser.add_argument("--guard-action", choices=("stop", "record"), default="stop")
    parser.add_argument("--task-criteria", type=Path)
    return parser


def prepare_run(args):
    source, report_path, output = args.usd.resolve(), args.report.resolve(), args.output.resolve()
    if args.mode == "task" and args.task_criteria is None:
        raise ValueError("Task mode requires an explicit --task-criteria file")
    if args.mode == "legacy" and (args.task_criteria is not None or args.guard_action != "stop"):
        raise ValueError("Legacy mode keeps its stop action and does not accept task criteria")
    criteria = load_task_criteria(args.task_criteria)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    matches = [item for item in report["physics"]["rigid_chains"] if item["chain_id"] == args.chain_id]
    if len(matches) != 1:
        raise ValueError("Report must select exactly one rigid chain")
    hz = report["physics"]["scene"]["rigid_chain_policy"]["physics_hz"]
    if type(hz) is not int or hz <= 0:
        raise ValueError("A positive saved scene frequency is required")
    if not math.isfinite(args.max_duration) or args.max_duration <= 0 or args.max_steps <= 0:
        raise ValueError("Positive finite duration/step budgets required")
    requested = .25 if args.duration is None else args.duration
    if args.steps is None and (not math.isfinite(requested) or requested <= 0):
        raise ValueError("Positive finite duration required")
    steps = args.steps if args.steps is not None else max(1, math.ceil(requested * hz - 1e-12))
    if steps <= 0 or steps > args.max_steps or steps / hz > args.max_duration + 1e-12:
        raise ValueError("Native smoke budget exceeded; explicitly raise --max-duration/--max-steps or request fewer steps")
    if criteria and criteria["required_duration_s"] > steps / hz + 1e-12:
        raise ValueError("Requested run is shorter than the predeclared task duration")
    if not source.is_file():
        raise FileNotFoundError(source)
    return source, report_path, output, report, matches[0], hz, steps, criteria


def assess_task(criteria, rows, prerequisites):
    if criteria is None:
        return {"status": "not_assessed", "passed": None, "checks": {}}
    checks = {key: bool(item) for key, item in prerequisites.items()}
    observed = {}
    checks["required_duration"] = bool(rows and rows[-1]["time_s"] + 1e-12 >= criteria["required_duration_s"])
    for key in ("max_joint_gap_m", "max_endpoint_gap_m", "max_free_extension_fraction"):
        if key in criteria:
            metric = "free_extension_fraction" if key == "max_free_extension_fraction" else key
            measured = max((row[metric] for row in rows), default=None)
            observed[key] = measured
            checks[key] = measured is not None and measured <= criteria[key]
    if "tip_bending" in criteria:
        bending = criteria["tip_bending"]
        axis = ("X", "Y", "Z").index(bending["axis"])
        tail = [row for row in rows if row["time_s"] > rows[-1]["time_s"] - bending["window_s"] + 1e-12] if rows else []
        signed = [row["free_tip_displacement_m"][axis] * bending["direction"] for row in tail]
        mean = float(np.mean(signed)) if signed else None
        observed["tip_bending"] = {"signed_mean_displacement_m": mean, "sample_count": len(tail),
                                    "axis": bending["axis"], "direction": bending["direction"],
                                    "window_s": bending["window_s"]}
        checks["tip_bending"] = mean is not None and mean > bending["minimum_mean_displacement_m"]
    passed = bool(checks and all(checks.values()))
    return {"status": "passed" if passed else "failed", "passed": passed, "task_id": criteria["task_id"],
            "basis": criteria["basis"], "criteria": criteria, "checks": checks, "observed": observed,
            "scope": "Only the predeclared task observations; not material calibration or general physical accuracy"}


def protocol_exit_code(result):
    if (result.get("runtime_exception") is not None or not result.get("configuration_consistency", {}).get("passed", False)
            or result.get("cleanup_errors")):
        return 2
    if result.get("mode") == "task":
        return 0 if result.get("runtime_complete") and result.get("task_acceptance", {}).get("passed") is True else 1
    return 0 if result.get("passed") else 1


def main(argv=None, shared_app=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "argv-json-base64":
        if len(argv) != 2 or len(argv[1]) > 100000:
            raise ValueError("Expected one bounded JSON/Base64 argument token")
        argv = json.loads(base64.b64decode(argv[1], validate=True).decode("utf-8"))
        if not isinstance(argv, list) or len(argv) > 256 or any(not isinstance(item, str) or "\x00" in item for item in argv):
            raise ValueError("Decoded arguments must be a bounded array of strings")
    args = create_parser().parse_args(argv)
    source, report_path, output, report, chain, hz, steps, criteria = prepare_run(args)
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    _dump(output / "registration.json", {"usd": str(source), "usd_sha256": _sha(source),
          "report": str(report_path), "report_sha256": _sha(report_path), "script_sha256": _sha(__file__),
          "script_path": str(Path(__file__).resolve()), "python_executable": sys.executable,
          "chain_id": args.chain_id, "physics_hz": hz, "steps": steps, "duration_s": steps / hz,
          "max_duration_s": args.max_duration, "max_steps": args.max_steps, "limits": LIMITS,
          "mode": args.mode, "guard_action": args.guard_action, "task_criteria": criteria,
          "task_criteria_sha256": _sha(args.task_criteria) if args.task_criteria else None,
          "scope": "bounded initialization/hold and structural observations; no force/torque or accuracy calibration"})
    app = shared_app
    sim = sim_view = view = cache_id = None
    attached = False
    layers, paths, histories, velocities, rows = {}, [], [], [], []
    registration = json.loads((output / "registration.json").read_text(encoding="utf-8"))
    events, configuration_errors, cleanup_errors = [], [], []
    mass_error = inertia_error = None
    all_finite = False
    result = {"schema_version": RESULT_SCHEMA, "passed": False, "mode": args.mode,
              "guard_action": args.guard_action, "physical_accuracy": "not_assessed",
              "static_equilibrium": "not_assessed", "runtime_exception": None, "stopped_by_guard": False}
    try:
        if app is None:
            from isaacsim import SimulationApp
            app = SimulationApp({"headless": True, "multi_gpu": False, "enable_crashreporter": False,
                  "extra_args": ["--/app/settings/persistent=false", "--/log/file=" + str(output / "isaac.kit.log")]})
        import carb
        import omni.usd
        import omni.physx
        import omni.timeline
        import omni.physics.tensors as tensors
        from pxr import Usd, UsdGeom, UsdPhysics, UsdUtils
        carb.settings.get_settings().set_bool("/physics/updateToUsd", True)
        carb.settings.get_settings().set_bool("/physics/suppressReadback", True)
        omni.timeline.get_timeline_interface().stop()
        if not omni.usd.get_context().open_stage(str(source)):
            raise RuntimeError("Could not open USD")
        stage = omni.usd.get_context().get_stage()
        layers = {layer.realPath: _sha(layer.realPath) for layer in stage.GetUsedLayers()
                  if layer.realPath and Path(layer.realPath).is_file()}
        stage.SetEditTarget(stage.GetSessionLayer())
        mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
        kgpu = float(UsdPhysics.GetStageKilogramsPerUnit(stage))
        scene = stage.GetPrimAtPath(report["physics"]["scene"]["prim_path"])
        if scene.GetAttribute("physxScene:timeStepsPerSecond").Get() != hz:
            raise ConfigurationMismatch("USD scene Hz differs from saved report; no override is permitted")
        root = stage.GetPrimAtPath(chain["prim_path"])
        if json.loads(root.GetAttribute("sca:physicsDeclaration").Get()) != chain["declaration"]:
            raise ConfigurationMismatch("USD declaration differs from report")
        links = sorted(chain["links"], key=lambda item: item["index"])
        actual_links = {str(prim.GetPath()) for prim in Usd.PrimRange(root) if prim.IsA(UsdGeom.Capsule)}
        actual_joints = {str(prim.GetPath()) for prim in Usd.PrimRange(root) if prim.IsA(UsdPhysics.Joint)}
        if actual_links != {item["path"] for item in links} or actual_joints != {item["path"] for item in chain["joints"]}:
            raise ConfigurationMismatch("Actual USD Capsule/joint census differs from report")
        paths = list(dict.fromkeys([item["path"] for item in links] +
                    [item["rigid_body_path"] for item in chain["endpoints"] if item.get("rigid_body_path")]))
        index = {path: i for i, path in enumerate(paths)}
        link_indices = np.array([index[item["path"]] for item in links], dtype=np.intp)
        for path in paths:
            if not stage.GetPrimAtPath(path).HasAPI(UsdPhysics.RigidBodyAPI):
                raise ConfigurationMismatch("Missing selected rigid body: " + path)
        joint_indices, joint_positions, world_mask, endpoint_mask = [], [], [], []
        for item in chain["joints"]:
            joint = UsdPhysics.Joint(stage.GetPrimAtPath(item["path"]))
            if not joint:
                raise ConfigurationMismatch("Missing reported joint")
            pair, positions, mask = [], [], []
            for side in (0, 1):
                targets = getattr(joint, f"GetBody{side}Rel")().GetTargets()
                body = str(targets[0]) if targets else None
                if body is not None and body not in index:
                    raise ConfigurationMismatch("Joint body outside selected chain/endpoint actors")
                pair.append(index.get(body, 0))
                positions.append(np.asarray(getattr(joint, f"GetLocalPos{side}Attr")().Get()) * mpu)
                mask.append(body is None)
            joint_indices.append(pair)
            joint_positions.append(positions)
            world_mask.append(mask)
            endpoint_mask.append(item["kind"] == "endpoint_fixed")
        joint_indices, joint_positions = np.asarray(joint_indices), np.asarray(joint_positions)
        world_mask, endpoint_mask = np.asarray(world_mask), np.asarray(endpoint_mask)
        expected_mass = np.array([item["mass_kg"] for item in links])
        expected_inertia = np.array([item["diagonal_inertia_kg_m2"] for item in links])
        if not np.isfinite(expected_mass).all() or not np.isfinite(expected_inertia).all() or np.any(expected_mass <= 0) or np.any(expected_inertia <= 0):
            raise ConfigurationMismatch("Report must contain positive finite mass/inertia")
        value = lambda item: item["value"] if isinstance(item, dict) else item
        free_start = value(chain["declaration"]["geometry"]["free_start"])
        free_end = value(chain["declaration"]["geometry"]["free_end"])
        free = np.array([i for i, item in enumerate(links) if item["arc_start_m"] >= free_start - 1e-10
                         and item["arc_end_m"] <= free_end + 1e-10], dtype=np.intp)
        if not len(free):
            raise ConfigurationMismatch("No free material intervals found in the saved chain")
        chord_lengths = np.array([item["chord_length_m"] for item in links])
        free_reference = float(chord_lengths[free].sum())
        if not math.isfinite(free_reference) or free_reference <= 0:
            raise ConfigurationMismatch("Positive finite free reference chord length required")
        initial_free_tip = np.asarray(links[free[-1]]["endpoint_1_m"])
        sim = omni.physx.get_physx_simulation_interface()
        cache_id = UsdUtils.StageCache.Get().Insert(stage)
        sid = cache_id.ToLongInt()
        sim.attach_stage(sid)
        attached = True
        for step in range(steps):
            sim.simulate(1 / hz, step / hz)
            sim.fetch_results()
            if view is None:
                sim_view = tensors.create_simulation_view("numpy", stage_id=sid)
                sim_view.set_subspace_roots("/")
                view = sim_view.create_rigid_body_view(paths)
                if view.count != len(paths) or set(view.prim_paths) != set(paths):
                    raise ConfigurationMismatch("Native body census differs from saved chain")
                order = [list(view.prim_paths).index(path) for path in paths]
                masses = np.asarray(view.get_masses()).reshape(-1)[order] * kgpu
                inertia = np.asarray(view.get_inertias()).reshape(-1, 3, 3)[order] * kgpu * mpu**2
                mass_error = float(np.max(abs(masses[link_indices] - expected_mass) / expected_mass))
                diagonal = np.diagonal(inertia[link_indices], axis1=1, axis2=2)
                inertia_error = float(np.max(abs(diagonal - expected_inertia) / expected_inertia))
                if not np.isfinite(masses).all() or not np.isfinite(inertia).all() or np.any(diagonal <= 0):
                    raise RuntimeError("Invalid native mass/inertia")
                _dump(output / "native-census.json", {"body_paths": paths, "native_body_paths": list(view.prim_paths),
                      "body_count": view.count, "link_count": len(links), "joint_count_in_usd": len(chain["joints"]),
                      "masses_kg": masses.tolist(), "inertias_COM_local_kg_m2": inertia.tolist(),
                      "mass_max_relative_error": mass_error, "inertia_max_relative_error": inertia_error,
                      "device_ordinal": sim_view.device_ordinal, "scene_policy": report["physics"]["scene"]["rigid_chain_policy"],
                      "joint_observation": "USD joint frames plus native body transforms; native drive impulses are not read"})
            poses = np.array(view.get_transforms(), copy=True)[order]
            speed = np.array(view.get_velocities(), copy=True)[order]
            poses[:, :3] *= mpu
            speed[:, :3] *= mpu
            if not np.isfinite(poses).all() or not np.isfinite(speed).all():
                all_finite = False
                raise RuntimeError("Non-finite native state")
            all_finite = True
            quaternions = poses[:, 3:]
            qerr = float(np.max(abs(np.linalg.norm(quaternions, axis=1) - 1)))
            if qerr > LIMITS["quaternion_norm_error"]:
                raise RuntimeError("Native quaternion normalization limit exceeded")
            anchors = poses[joint_indices, :3] + _rotate(quaternions[joint_indices], joint_positions)
            anchors[world_mask] = joint_positions[world_mask]
            gaps = np.linalg.norm(anchors[:, 0] - anchors[:, 1], axis=1)
            directions = _rotate(quaternions[link_indices], np.array([1., 0., 0.]))
            starts = poses[link_indices, :3] - directions * chord_lengths[:, None] / 2
            ends = poses[link_indices, :3] + directions * chord_lengths[:, None] / 2
            bridges = np.linalg.norm(starts[free[1:]] - ends[free[:-1]], axis=1)
            free_extension = float(bridges.sum() / free_reference)
            row = {"time_s": (step + 1) / hz, "max_joint_gap_m": float(gaps.max()),
                   "max_endpoint_gap_m": float(gaps[endpoint_mask].max()) if endpoint_mask.any() else 0.,
                   "max_speed_m_s": float(np.linalg.norm(speed[:, :3], axis=1).max()), "quaternion_norm_error": qerr,
                   "free_reference_chord_m": free_reference, "free_effective_length_m": free_reference * (1 + free_extension),
                   "free_extension_fraction": free_extension,
                   "free_tip_world_m": ends[free[-1]].tolist(),
                   "free_tip_displacement_m": (ends[free[-1]] - initial_free_tip).tolist()}
            rows.append(row)
            histories.append(poses)
            velocities.append(speed)
            current_events = []
            for name, observed, category in (
                    ("joint_gap_m", row["max_joint_gap_m"], "engineering_guard"),
                    ("speed_m_s", row["max_speed_m_s"], "engineering_guard"),
                    ("mass_relative_error", mass_error, "configuration_consistency"),
                    ("inertia_relative_error", inertia_error, "configuration_consistency")):
                if observed > LIMITS[name]:
                    current_events.append({"step": step + 1, "time_s": row["time_s"], "check": name,
                                           "observed": observed, "limit": LIMITS[name], "category": category})
            events.extend(current_events)
            if any(event["category"] == "configuration_consistency" for event in current_events):
                raise ConfigurationMismatch("Native mass/inertia differs from the saved proxy values")
            if current_events and args.guard_action == "stop":
                raise EngineeringGuardStop("Bounded smoke structural/mass/inertia/speed limit exceeded")
            if not app.is_running():
                raise RuntimeError("Isaac closed before completion")
        result.update(completed_steps=len(rows), duration_s=steps / hz,
                      max_joint_gap_m=max(row["max_joint_gap_m"] for row in rows),
                      max_endpoint_gap_m=max(row["max_endpoint_gap_m"] for row in rows),
                      max_speed_m_s=max(row["max_speed_m_s"] for row in rows),
                      quaternion_norm_error=max(row["quaternion_norm_error"] for row in rows),
                      mass_max_relative_error=mass_error, inertia_max_relative_error=inertia_error)
    except EngineeringGuardStop:
        result.update(error=traceback.format_exc(), completed_steps=len(rows), stopped_by_guard=True)
    except ConfigurationMismatch as exc:
        configuration_errors.append(str(exc))
        result.update(error=traceback.format_exc(), completed_steps=len(rows))
    except Exception as exc:
        result["runtime_exception"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        result.update(error=traceback.format_exc(), completed_steps=len(rows))
    finally:
        def cleanup(label, operation):
            try:
                operation()
            except Exception as exc:
                cleanup_errors.append({"operation": label, "type": type(exc).__name__, "message": str(exc)})

        if rows:
            cleanup("save_history", lambda: np.savez_compressed(output / "native-history.npz",
                    time_s=np.array([row["time_s"] for row in rows]), poses_world_m_xyzw=np.asarray(histories),
                    velocities_SI_m_s_rad_s=np.asarray(velocities), body_paths=np.asarray(paths)))
            cleanup("save_metrics", lambda: _dump(output / "metrics.json", rows))
        if sim_view is not None:
            cleanup("invalidate_tensor_view", sim_view.invalidate)
        if attached:
            cleanup("detach_stage", sim.detach_stage)
        if cache_id is not None:
            cleanup("close_stage", omni.usd.get_context().close_stage)
            def erase_cache():
                cache = UsdUtils.StageCache.Get()
                if cache.Contains(cache_id):
                    cache.Erase(cache_id)
            cleanup("erase_stage_cache", erase_cache)
        result["source_layers_sha256"] = layers
        def unchanged(path, expected):
            try:
                return Path(path).is_file() and _sha(path) == expected
            except OSError:
                return False
        source_in_layers = any(Path(path).resolve() == source for path in layers)
        result["source_layers_unchanged"] = bool(layers) and source_in_layers and all(unchanged(path, digest) for path, digest in layers.items())
        source_unchanged = unchanged(source, registration["usd_sha256"])
        report_unchanged = unchanged(report_path, registration["report_sha256"])
        criteria_unchanged = args.task_criteria is None or unchanged(args.task_criteria, registration["task_criteria_sha256"])
        consistency = {"source_layers_checked": bool(layers) and source_in_layers,
                       "source_layers_unchanged": result["source_layers_unchanged"],
                       "input_usd_unchanged": source_unchanged, "report_unchanged": report_unchanged,
                       "task_criteria_unchanged": criteria_unchanged,
                       "saved_and_native_configuration": not configuration_errors and mass_error is not None and inertia_error is not None}
        result["configuration_consistency"] = {"passed": all(consistency.values()), "checks": consistency,
                                                "errors": configuration_errors}
        result["integration_completed"] = len(rows) == steps
        result["runtime_complete"] = len(rows) == steps and result["runtime_exception"] is None and not cleanup_errors
        result["completed_steps"] = len(rows)
        result["requested_steps"] = steps
        result["duration_s"] = rows[-1]["time_s"] if rows else 0.
        result["requested_duration_s"] = steps / hz
        result["all_native_finite"] = all_finite and bool(rows)
        result["cleanup_errors"] = cleanup_errors
        engineering_events = [event for event in events if event["category"] == "engineering_guard"]
        result["engineering_guards"] = {"passed": not engineering_events, "limits": LIMITS,
                                         "event_count": len(engineering_events), "events": engineering_events,
                                         "interpretation": "Engineering observations, not universal physical limits; speed alone does not establish a simulation fault"}
        result["passed"] = bool(result["runtime_complete"] and not result["stopped_by_guard"] and not events
                                and result["configuration_consistency"]["passed"])
        result["task_acceptance"] = assess_task(criteria, rows, {
            "runtime_complete": result["runtime_complete"], "all_native_finite": result["all_native_finite"],
            "configuration_consistency": result["configuration_consistency"]["passed"],
            "not_stopped_by_guard": not result["stopped_by_guard"]})
        if rows:
            for key in ("max_joint_gap_m", "max_endpoint_gap_m", "max_speed_m_s", "quaternion_norm_error"):
                result[key] = max(row[key] for row in rows)
            result["max_free_extension_fraction"] = max(row["free_extension_fraction"] for row in rows)
        result["mass_max_relative_error"] = mass_error
        result["inertia_max_relative_error"] = inertia_error
        result["elapsed_seconds"] = time.perf_counter() - started
        result["scope"] = "finite native initialization/hold and structural check; no theory, equilibrium or calibration PASS"
        result["protocol_exit_code"] = protocol_exit_code(result)
        _dump(output / "guard-events.json", events)
        _dump(output / "task-acceptance.json", result["task_acceptance"])
        _dump(output / "summary.json", result)
        if shared_app is None and app is not None:
            app.close()
    print(json.dumps(result, ensure_ascii=True, allow_nan=False), flush=True)
    return result["protocol_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
