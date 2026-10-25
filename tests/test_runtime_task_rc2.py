"""Protocol/criteria tests use synthetic evidence only; never start Isaac."""
import importlib.util
import json
from pathlib import Path
import re
import base64
import sys
import types

import numpy as np
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location("test_" + name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load("cable_runtime_smoke")
launcher = load("run_cable_runtime")


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def inputs(tmp_path, *, minimum=.00001):
    source = tmp_path / "input.usda"
    source.write_text("#usda 1.0\n# synthetic unit-test input; no simulator\n", encoding="utf-8")
    links = [{"path": f"/Chain/link_{i}", "index": i, "mass_kg": .001,
              "diagonal_inertia_kg_m2": [1e-7, 1e-7, 1e-7], "chord_length_m": .1,
              "arc_start_m": i * .1, "arc_end_m": (i + 1) * .1,
              "endpoint_1_m": [(i + 1) * .1, 0, 0]} for i in range(2)]
    joints = [{"kind": "internal_d6", "body0": [links[0]["path"]], "body1": [links[1]["path"]],
               "local_position_0_m": [.05, 0, 0], "local_position_1_m": [-.05, 0, 0]},
              {"kind": "endpoint_fixed", "body0": [], "body1": [links[0]["path"]],
               "local_position_0_m": [0, 0, 0], "local_position_1_m": [-.05, 0, 0]}]
    chain = {"chain_id": "wire", "links": links, "joints": joints, "endpoints": [],
             "declaration": {"geometry": {"free_start": {"value": 0}, "free_end": {"value": .2}}}}
    report = tmp_path / "input.report.json"
    write(report, {"physics": {"rigid_chains": [chain], "scene": {"rigid_chain_policy": {"physics_hz": 2}}}})
    criteria = tmp_path / "criteria.json"
    write(criteria, {"schema_version": "1.0", "task_id": "synthetic-direction-test", "basis": "Unit-test signal only, no physics claim",
                     "required_duration_s": 1, "max_joint_gap_m": .0005, "max_endpoint_gap_m": .0005,
                     "max_free_extension_fraction": .05,
                     "tip_bending": {"axis": "Z", "direction": -1, "window_s": 1, "minimum_mean_displacement_m": minimum}})
    return source, report, criteria


def synthetic_evidence(tmp_path, *, speed=0., minimum=.00001):
    source, report, criteria = inputs(tmp_path, minimum=minimum)
    output = tmp_path / "launch"
    native = output / "native"
    native.mkdir(parents=True)
    arguments = ["--usd", str(source), "--report", str(report), "--output", str(native), "--chain-id", "wire",
                 "--duration", "1", "--max-duration", "1", "--max-steps", "2", "--mode", "task",
                 "--guard-action", "record", "--task-criteria", str(criteria)]
    args = smoke.create_parser().parse_args(arguments)
    plan = smoke.prepare_run(args)
    hashes = {"usd": smoke._sha(source), "report": smoke._sha(report), "checker": smoke._sha(launcher.CHECKER),
              "criteria": smoke._sha(criteria), "criteria_path": str(criteria), "mode": "task", "guard_action": "record"}
    poses = np.zeros((2, 2, 7), dtype=np.float32)
    poses[..., 6] = 1
    poses[:, 0, 0], poses[:, 1, 0] = .05, .15
    poses[:, 1, 2] = -.0001
    velocity = np.zeros((2, 2, 6), dtype=np.float32)
    velocity[..., 0] = speed
    paths = [link["path"] for link in plan[4]["links"]]
    np.savez_compressed(native / "native-history.npz", time_s=np.array([.5, 1.]), poses_world_m_xyzw=poses,
                        velocities_SI_m_s_rad_s=velocity, body_paths=np.array(paths))
    rows = [{"time_s": t, "max_joint_gap_m": .0001, "max_endpoint_gap_m": 0., "max_speed_m_s": speed,
             "free_extension_fraction": .0005, "free_tip_displacement_m": [0., 0., -.0001]} for t in (.5, 1.)]
    write(native / "metrics.json", rows)
    write(native / "guard-events.json", [{"category": "engineering_guard", "check": "speed_m_s", "time_s": t,
                                           "observed": speed, "limit": 10.} for t in (.5, 1.)] if speed > 10 else [])
    write(native / "native-census.json", {"body_count": 2, "body_paths": paths, "masses_kg": [.001, .001],
          "inertias_COM_local_kg_m2": [np.diag([1e-7] * 3).tolist()] * 2})
    registration = {"usd": str(source.resolve()), "report": str(report.resolve()), "chain_id": "wire", "steps": 2,
                    "physics_hz": 2, "duration_s": 1., "limits": smoke.LIMITS, "mode": "task", "guard_action": "record",
                    "task_criteria": plan[-1], "script_sha256": hashes["checker"], "usd_sha256": hashes["usd"],
                    "report_sha256": hashes["report"], "task_criteria_sha256": hashes["criteria"]}
    write(native / "registration.json", registration)
    prerequisites = {"runtime_complete": True, "all_native_finite": True, "configuration_consistency": True, "not_stopped_by_guard": True}
    summary = {"schema_version": smoke.RESULT_SCHEMA, "mode": "task", "guard_action": "record", "passed": speed <= 10,
               "runtime_exception": None, "runtime_complete": True, "all_native_finite": True, "completed_steps": 2,
               "source_layers_sha256": {str(source.resolve()): hashes["usd"]}, "source_layers_unchanged": True,
               "configuration_consistency": {"passed": True}, "cleanup_errors": [],
               "engineering_guards": {"event_count": 2 if speed > 10 else 0},
               "task_acceptance": smoke.assess_task(plan[-1], rows, prerequisites)}
    summary["protocol_exit_code"] = smoke.protocol_exit_code(summary)
    write(native / "summary.json", summary)
    return output, plan, hashes, summary


def test_unchanged_legacy_limits():
    assert smoke.LIMITS == {"joint_gap_m": .0005, "quaternion_norm_error": 1e-4,
                            "mass_relative_error": 1e-4, "inertia_relative_error": 1e-4, "speed_m_s": 10.0}


def test_speed_warning_alone_does_not_override_predeclared_task(tmp_path):
    output, plan, hashes, summary = synthetic_evidence(tmp_path, speed=11.)
    result = launcher.assess_evidence(output, plan, hashes, 0)
    assert summary["passed"] is False
    assert result["exit_code"] == 0 and result["evidence_valid"] is True
    assert result["legacy_passed"] is False and result["engineering_warning_count"] == 2
    assert result["task_acceptance"]["passed"] is True


def test_zero_child_exit_cannot_hide_task_failure(tmp_path):
    output, plan, hashes, _ = synthetic_evidence(tmp_path, minimum=.001)
    result = launcher.assess_evidence(output, plan, hashes, 0)
    assert result["exit_code"] == 1
    assert result["task_acceptance"]["checks"]["tip_bending"] is False


def test_parent_detects_changed_input_even_with_passing_child(tmp_path):
    output, plan, hashes, _ = synthetic_evidence(tmp_path)
    plan[0].write_text("changed synthetic source", encoding="utf-8")
    result = launcher.assess_evidence(output, plan, hashes, 0)
    assert result["exit_code"] == 2 and result["checks"]["input_usd_unchanged"] is False


def test_parent_reconstructs_bending_instead_of_trusting_metrics(tmp_path):
    output, plan, hashes, summary = synthetic_evidence(tmp_path)
    rows = launcher._read(plan[2] / "metrics.json")
    for row in rows:
        row["free_tip_displacement_m"][2] = -.01
    write(plan[2] / "metrics.json", rows)
    result = launcher.assess_evidence(output, plan, hashes, 0)
    assert result["exit_code"] == 2 and result["checks"]["metrics_geometry_matches_npz"] is False


def test_missing_npz_cannot_be_a_success(tmp_path):
    output, plan, hashes, _ = synthetic_evidence(tmp_path)
    (plan[2] / "native-history.npz").unlink()
    with pytest.raises(FileNotFoundError):
        launcher.assess_evidence(output, plan, hashes, 0)


def test_child_failure_conflicting_with_passing_report_is_error(tmp_path):
    output, plan, hashes, _ = synthetic_evidence(tmp_path)
    assert launcher.assess_evidence(output, plan, hashes, 7)["exit_code"] == 2


def test_budget_rejection_never_starts_isaac(tmp_path, monkeypatch):
    source, report, criteria = inputs(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected budget must not start an Isaac process")
    monkeypatch.setattr(launcher, "run_process", forbidden)
    output = tmp_path / "rejected"
    code = launcher.main(["--isaac-python", sys.executable, "--usd", str(source), "--report", str(report),
                          "--output", str(output), "--chain-id", "wire", "--duration", "5"])
    assert code == 2
    assert launcher._read(output / "launcher.json")["isaac_launched"] is False
    assert launcher._read(output / "assessment.json")["status"] == "rejected_before_isaac"
    assert not (output / "native").exists()


def test_runtime_exception_is_separate_from_engineering_warning(tmp_path, monkeypatch):
    source, report, _ = inputs(tmp_path)
    def unavailable(*args, **kwargs):
        raise ImportError("Synthetic unavailable host; this test never starts Isaac")
    monkeypatch.setitem(sys.modules, "isaacsim", types.SimpleNamespace(SimulationApp=unavailable))
    output = tmp_path / "no-host"
    code = smoke.main(["--usd", str(source), "--report", str(report), "--output", str(output), "--chain-id", "wire"])
    summary = launcher._read(output / "summary.json")
    assert code == 2
    assert summary["runtime_exception"]["type"] == "ImportError"
    assert summary["engineering_guards"]["event_count"] == 0
    assert summary["source_layers_unchanged"] is False  # no vacuous all(empty)


@pytest.mark.parametrize("change", [
    {"extra": True}, {"required_duration_s": True}, {"required_duration_s": float("nan")},
    {"tip_bending": {"axis": "Z", "direction": 0, "window_s": 1, "minimum_mean_displacement_m": 0}},
])
def test_invalid_criteria_are_rejected(tmp_path, change):
    _, _, path = inputs(tmp_path)
    value = launcher._read(path)
    value.update(change)
    write(path, value)
    with pytest.raises(ValueError):
        smoke.load_task_criteria(path)


def test_legacy_cannot_silently_enable_record_mode(tmp_path):
    source, report, _ = inputs(tmp_path)
    args = smoke.create_parser().parse_args(["--usd", str(source), "--report", str(report), "--output", str(tmp_path / "unused"),
                                            "--chain-id", "wire", "--guard-action", "record"])
    with pytest.raises(ValueError, match="Legacy mode"):
        smoke.prepare_run(args)


def test_batch_transport_keeps_data_out_of_cmd(tmp_path):
    values = ["--usd", "F:/data/中文 & O'Brien %MODEL%/asset.usda"]
    batch = tmp_path / "Isaac host.bat"
    command = launcher.build_command(batch, launcher.CHECKER, values)
    assert '"-I"' not in command if isinstance(command, str) else "-I" not in command
    if isinstance(command, str):
        assert values[1] not in command
        token = re.search(r'"argv-json-base64"\s+"([A-Za-z0-9+/=]+)"', command).group(1)
    else:
        token = command[-1]
    assert json.loads(base64.b64decode(token)) == values


def test_tip_threshold_uses_strict_exceedance():
    criteria = {"task_id": "boundary", "basis": "test", "required_duration_s": 1,
                "tip_bending": {"axis": "Z", "direction": -1, "window_s": 1, "minimum_mean_displacement_m": .001}}
    result = smoke.assess_task(criteria, [{"time_s": 1., "free_tip_displacement_m": [0., 0., -.001]}], {"runtime_complete": True})
    assert result["checks"]["tip_bending"] is False
