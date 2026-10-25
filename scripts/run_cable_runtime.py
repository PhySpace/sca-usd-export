"""Launch bounded Isaac checks and derive an exit status from saved evidence.

Run with the add-on Python (NumPy is a declared dependency). Isaac remains an
explicit external host. This launcher never modifies the input USD or report.
"""
import base64
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback


CHECKER = Path(__file__).resolve().with_name("cable_runtime_smoke.py")
_spec = importlib.util.spec_from_file_location("sca_cable_runtime_checker", CHECKER)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


def _read(path):
    def invalid(value):
        raise ValueError("Non-finite JSON value: " + value)
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid)


def build_command(isaac_python, checker, arguments):
    token = base64.b64encode(json.dumps(arguments, ensure_ascii=False).encode("utf-8")).decode("ascii")
    # Isaac's official batch sets vendor PYTHONPATH. -I would discard that
    # declared host setup (including NumPy); exclude user-site with -s instead.
    values = [str(Path(isaac_python).resolve()), "-s", "-B", str(Path(checker).resolve()), "argv-json-base64", token]
    if os.name == "nt" and Path(isaac_python).suffix.lower() in (".bat", ".cmd"):
        # Only installation paths and a Base64 token reach cmd. Data paths,
        # including &, quotes and %, remain encoded until inside Python.
        if any(any(c in item for c in '\r\n"%!') for item in (values[0], values[3])):
            raise ValueError("Batch host/checker installation paths cannot contain quotes, %, ! or line breaks")
        if len(token) > 6000:
            raise ValueError("Windows batch transport exceeds 6000 Base64 characters; shorten paths")
        comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
        return subprocess.list2cmdline([comspec, "/d", "/v:off", "/s", "/c"]) + ' "' + " ".join('"' + item + '"' for item in values) + '"'
    return values


def run_process(command, output, timeout, on_started=None):
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONEXE"):
        environment.pop(key, None)
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen(command, cwd=output, env=environment, stdout=stdout, stderr=stderr, **options)
        if on_started is not None:
            on_started(process.pid)
        try:
            return process.pid, process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
            else:
                process.kill()
            process.wait(timeout=30)
            raise RuntimeError("Isaac host exceeded the explicit wall-clock budget")


def assess_evidence(output, plan, input_hashes, child_returncode):
    """Validate files produced by the child; child exit zero is insufficient."""
    source, report_path, native, report, chain, hz, steps, criteria = plan
    summary = _read(native / "summary.json")
    registration = _read(native / "registration.json")
    checks = {
        "summary_schema": summary.get("schema_version") == smoke.RESULT_SCHEMA,
        "summary_mode": summary.get("mode") == registration.get("mode") == input_hashes["mode"],
        "registered_guard_action": registration.get("guard_action") == input_hashes["guard_action"] == summary.get("guard_action"),
        "registered_source": Path(registration.get("usd", "")).resolve() == source,
        "registered_report": Path(registration.get("report", "")).resolve() == report_path,
        "registered_chain": registration.get("chain_id") == chain["chain_id"],
        "registered_steps": registration.get("steps") == steps and registration.get("physics_hz") == hz,
        "registered_duration": registration.get("duration_s") == steps / hz,
        "unchanged_limits": registration.get("limits") == smoke.LIMITS,
        "registered_criteria": registration.get("task_criteria") == criteria,
        "registered_checker": registration.get("script_sha256") == input_hashes["checker"],
        "registered_input_hashes": registration.get("usd_sha256") == input_hashes["usd"] and registration.get("report_sha256") == input_hashes["report"],
        "input_usd_unchanged": smoke._sha(source) == input_hashes["usd"],
        "input_report_unchanged": smoke._sha(report_path) == input_hashes["report"],
        "checker_unchanged": smoke._sha(CHECKER) == input_hashes["checker"],
        "source_layers_checked": bool(summary.get("source_layers_sha256")),
        "source_layers_unchanged": summary.get("source_layers_unchanged") is True,
    }
    if criteria is not None:
        checks["registered_criteria_hash"] = registration.get("task_criteria_sha256") == input_hashes["criteria"]
        checks["criteria_file_unchanged"] = smoke._sha(input_hashes["criteria_path"]) == input_hashes["criteria"]
    layers = summary.get("source_layers_sha256", {})
    checks["parent_source_layers_unchanged"] = bool(layers) and all(
        Path(path).is_file() and smoke._sha(path) == digest for path, digest in layers.items())
    checks["root_source_in_checked_layers"] = any(Path(path).resolve() == source for path in layers)
    count = summary.get("completed_steps")
    checks["completed_steps_valid"] = type(count) is int and 0 <= count <= steps
    if not checks["completed_steps_valid"]:
        raise ValueError("Invalid child completed_steps")
    rows = _read(native / "metrics.json") if count else []
    reconstructed = rows
    native_engineering_events = None
    checks["metrics_count"] = isinstance(rows, list) and len(rows) == count
    if count:
        census = _read(native / "native-census.json")
        expected_paths = list(dict.fromkeys([item["path"] for item in chain["links"]] +
                    [item["rigid_body_path"] for item in chain["endpoints"] if item.get("rigid_body_path")]))
        checks["body_census"] = census.get("body_count") == len(expected_paths) and set(census.get("body_paths", [])) == set(expected_paths)
        census_paths = census.get("body_paths", [])
        link_ids = [census_paths.index(link["path"]) for link in chain["links"]]
        masses = smoke.np.asarray(census["masses_kg"])
        inertias = smoke.np.asarray(census["inertias_COM_local_kg_m2"])
        expected_mass = smoke.np.asarray([link["mass_kg"] for link in chain["links"]])
        expected_inertia = smoke.np.asarray([link["diagonal_inertia_kg_m2"] for link in chain["links"]])
        checks["parent_mass_agreement"] = bool(smoke.np.max(abs(masses[link_ids] - expected_mass) / expected_mass) <= smoke.LIMITS["mass_relative_error"])
        checks["parent_inertia_agreement"] = bool(smoke.np.max(abs(smoke.np.diagonal(inertias[link_ids], axis1=1, axis2=2) - expected_inertia) / expected_inertia) <= smoke.LIMITS["inertia_relative_error"])
        with smoke.np.load(native / "native-history.npz", allow_pickle=False) as data:
            times, poses, velocities, paths = (data[key] for key in ("time_s", "poses_world_m_xyzw", "velocities_SI_m_s_rad_s", "body_paths"))
            checks["npz_shapes"] = poses.shape == (count, len(expected_paths), 7) and velocities.shape == (count, len(expected_paths), 6) and times.shape == (count,)
            checks["npz_body_paths"] = len(paths) == len(expected_paths) and set(paths.tolist()) == set(expected_paths)
            checks["npz_finite"] = bool(smoke.np.isfinite(times).all() and smoke.np.isfinite(poses).all() and smoke.np.isfinite(velocities).all())
            checks["npz_time_steps"] = bool(smoke.np.allclose(times, smoke.np.arange(1, count + 1) / hz, rtol=0, atol=1e-12))
            checks["metrics_time_steps"] = bool(len(rows) == count and smoke.np.allclose([row["time_s"] for row in rows], times, rtol=0, atol=1e-12))
            checks["npz_quaternions"] = bool(smoke.np.max(abs(smoke.np.linalg.norm(poses[..., 3:], axis=-1) - 1)) <= smoke.LIMITS["quaternion_norm_error"])
            if not all(checks[key] for key in ("npz_shapes", "npz_body_paths", "npz_finite", "npz_time_steps", "metrics_time_steps", "npz_quaternions")):
                raise ValueError("Native history has invalid shapes, identity, time, finite state or quaternion evidence")
            # Reconstruct task quantities from NPZ, not the child's verdict or
            # its metrics alone. Float32/serialized-frame rounding is checked
            # separately from all unchanged physical/engineering limits.
            paths = paths.tolist()
            links = sorted(chain["links"], key=lambda link: link["index"])
            ids = [paths.index(link["path"]) for link in links]
            lengths = smoke.np.asarray([link["chord_length_m"] for link in links])
            directions = smoke._rotate(poses[:, ids, 3:], smoke.np.array([1., 0., 0.]))
            starts = poses[:, ids, :3] - directions * lengths[None, :, None] / 2
            ends = poses[:, ids, :3] + directions * lengths[None, :, None] / 2
            value = lambda item: item["value"] if isinstance(item, dict) else item
            geometry = chain["declaration"]["geometry"]
            free = [i for i, link in enumerate(links) if link["arc_start_m"] >= value(geometry["free_start"]) - 1e-10
                    and link["arc_end_m"] <= value(geometry["free_end"]) + 1e-10]
            reference = float(lengths[free].sum())
            extension = smoke.np.linalg.norm(starts[:, free[1:]] - ends[:, free[:-1]], axis=-1).sum(axis=1) / reference
            displacement = ends[:, free[-1]] - smoke.np.asarray(links[free[-1]]["endpoint_1_m"])
            gaps, endpoint_gaps = [], []
            for joint in chain["joints"]:
                anchors = []
                for side in (0, 1):
                    bodies = joint[f"body{side}"]
                    local = smoke.np.asarray(joint[f"local_position_{side}_m"])
                    anchors.append(smoke.np.broadcast_to(local, (count, 3)) if not bodies else
                                   poses[:, paths.index(bodies[0]), :3] + smoke._rotate(poses[:, paths.index(bodies[0]), 3:], local))
                gap = smoke.np.linalg.norm(anchors[0] - anchors[1], axis=-1)
                gaps.append(gap)
                if joint["kind"] == "endpoint_fixed":
                    endpoint_gaps.append(gap)
            max_gaps = smoke.np.asarray(gaps).max(axis=0)
            max_endpoint = smoke.np.asarray(endpoint_gaps).max(axis=0) if endpoint_gaps else smoke.np.zeros(count)
            max_speed = smoke.np.linalg.norm(velocities[..., :3], axis=-1).max(axis=1)
            native_engineering_events = int(smoke.np.count_nonzero(max_gaps > smoke.LIMITS["joint_gap_m"]) +
                                            smoke.np.count_nonzero(max_speed > smoke.LIMITS["speed_m_s"]))
            guard_events = _read(native / "guard-events.json")
            checks["engineering_events_match_native"] = (summary.get("engineering_guards", {}).get("event_count") == native_engineering_events
                and len([event for event in guard_events if event.get("category") == "engineering_guard"]) == native_engineering_events)
            world_scale = max(float(smoke.np.max(abs(poses[..., :3]))), float(lengths.max()), 1e-6)
            tolerance_m = max(1e-12, 8 * float(smoke.np.finfo(smoke.np.float32).eps) * world_scale)
            checks["metrics_geometry_matches_npz"] = all(smoke.np.allclose(actual, expected, rtol=0, atol=tolerance) for actual, expected, tolerance in (
                ([row["max_joint_gap_m"] for row in rows], max_gaps, tolerance_m),
                ([row["max_endpoint_gap_m"] for row in rows], max_endpoint, tolerance_m),
                ([row["free_extension_fraction"] for row in rows], extension, tolerance_m * max(1, len(free) - 1) / reference),
                ([row["free_tip_displacement_m"] for row in rows], displacement, tolerance_m),
                ([row["max_speed_m_s"] for row in rows], max_speed, max(1e-12, 8 * float(smoke.np.finfo(smoke.np.float32).eps) * max(1., float(max_speed.max()))))))
            reconstructed = [{"time_s": float(times[i]), "max_joint_gap_m": float(max_gaps[i]),
                              "max_endpoint_gap_m": float(max_endpoint[i]), "free_extension_fraction": float(extension[i]),
                              "free_tip_displacement_m": displacement[i].tolist()} for i in range(count)]
    else:
        checks["npz_finite"] = False
    complete = count == steps and summary.get("runtime_complete") is True
    prerequisites = {"runtime_complete": complete,
                     "all_native_finite": checks["npz_finite"] and summary.get("all_native_finite") is True,
                     "configuration_consistency": summary.get("configuration_consistency", {}).get("passed") is True,
                     "not_stopped_by_guard": not summary.get("stopped_by_guard", False)}
    acceptance = smoke.assess_task(criteria, reconstructed, prerequisites)
    child_acceptance = summary.get("task_acceptance", {})
    checks["parent_task_verdict_matches"] = all(acceptance.get(key) == child_acceptance.get(key) for key in ("status", "passed", "checks", "task_id", "criteria", "basis"))
    expected_exit = smoke.protocol_exit_code(summary)
    if count:
        expected_legacy = bool(complete and native_engineering_events == 0 and all(prerequisites.values())
                               and not summary.get("cleanup_errors") and checks["parent_mass_agreement"] and checks["parent_inertia_agreement"])
        checks["legacy_boolean_matches_native"] = summary.get("passed") is expected_legacy
    checks["child_protocol_exit_matches"] = summary.get("protocol_exit_code") == expected_exit
    # Kit can exit(0) from app.close before main's nonzero return. A nonzero
    # child code contradictory to a successful report is still an error.
    checks["child_exit_compatible"] = child_returncode in (0, expected_exit)
    valid = all(checks.values())
    code = expected_exit if valid else 2
    files = {str(path.relative_to(output)): smoke._sha(path) for path in native.iterdir() if path.is_file() and path.suffix in (".json", ".npz")}
    return {"schema_version": smoke.RESULT_SCHEMA, "exit_code": code,
            "status": "completed" if code == 0 else "task_or_legacy_guard_failed" if code == 1 else "runtime_configuration_or_evidence_error",
            "evidence_valid": valid, "checks": checks, "runtime_complete": complete,
            "task_acceptance": acceptance, "legacy_passed": summary.get("passed"),
            "engineering_warning_count": native_engineering_events,
            "runtime_exception": summary.get("runtime_exception"), "child_process_exit_code": child_returncode,
            "summary": str(native / "summary.json"), "evidence_files_sha256": files,
            "metric_reconstruction_tolerance_m": tolerance_m if count else None,
            "physical_accuracy": "not_assessed"}


def main(argv=None):
    parser = smoke.create_parser()
    parser.description = __doc__
    parser.add_argument("--isaac-python", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=600, help="wall-clock seconds; includes host startup")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    launch = {"schema_version": smoke.RESULT_SCHEMA, "isaac_launched": False, "launch_attempted": False,
              "output": str(output), "launcher_sha256": smoke._sha(__file__),
              "scope": "Parent-process evidence/exit verification, not a physics solver or accuracy certification"}
    assessment = None
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("Positive finite wall-clock timeout required")
        if not args.isaac_python.is_file():
            raise FileNotFoundError(args.isaac_python)
        args.output = output / "native"
        plan = smoke.prepare_run(args)
        source, report_path, native, report, chain, hz, steps, criteria = plan
        hashes = {"usd": smoke._sha(source), "report": smoke._sha(report_path), "checker": smoke._sha(CHECKER),
                  "mode": args.mode, "guard_action": args.guard_action}
        if args.task_criteria:
            hashes.update(criteria=smoke._sha(args.task_criteria), criteria_path=str(args.task_criteria.resolve()))
        values = ["--usd", str(source), "--report", str(report_path), "--chain-id", args.chain_id,
                  "--output", str(native), "--max-duration", str(args.max_duration), "--max-steps", str(args.max_steps),
                  "--mode", args.mode, "--guard-action", args.guard_action]
        values += ["--steps", str(args.steps)] if args.steps is not None else ["--duration", str(.25 if args.duration is None else args.duration)]
        if args.task_criteria:
            values += ["--task-criteria", str(args.task_criteria.resolve())]
        command = build_command(args.isaac_python, CHECKER, values)
        launch.update(command=command, decoded_checker_arguments=values, input_hashes=hashes,
                      requested_steps=steps, requested_duration_s=steps / hz, timeout_s=args.timeout, launch_attempted=True)
        smoke._dump(output / "launcher.json", launch)
        def on_started(pid):
            launch.update(pid=pid, isaac_launched=True)
            smoke._dump(output / "launcher.json", launch)
        pid, child_code = run_process(command, output, args.timeout, on_started)
        launch.update(pid=pid, child_process_exit_code=child_code)
        assessment = assess_evidence(output, plan, hashes, child_code)
    except Exception as exc:
        assessment = {"schema_version": smoke.RESULT_SCHEMA, "exit_code": 2,
                      "status": "runtime_or_evidence_error" if launch["isaac_launched"] else "rejected_before_isaac",
                      "runtime_exception": {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()},
                      "physical_accuracy": "not_assessed", "evidence_valid": False}
    finally:
        launch["elapsed_seconds"] = time.perf_counter() - started
        smoke._dump(output / "launcher.json", launch)
        smoke._dump(output / "assessment.json", assessment)
    print(json.dumps(assessment, ensure_ascii=True, allow_nan=False), flush=True)
    return assessment["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
