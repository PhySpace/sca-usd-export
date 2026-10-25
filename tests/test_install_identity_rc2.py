"""Code identity rejection tests; fake files are never imported as business modules."""
import json
from pathlib import Path
import runpy
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
H = runpy.run_path(str(ROOT / "scripts/install_identity.py"))


def fixture_install(tmp_path, monkeypatch, *, usd=False):
    payload = tmp_path / ("sca-usd-export" if usd else "sca-physics")
    name = payload.name
    module = name.replace("-", "_")
    package = payload / "src" / module
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise RuntimeError('must not import before identity check')\n")
    requirements = 'dependencies = ["sca-physics==0.1.7rc2"]\n' if usd else 'dependencies = []\n'
    (payload / "pyproject.toml").write_text('[project]\nname = "' + name + '"\nversion = "0.1.7rc2"\n' + requirements)
    runtime = tmp_path / "runtime"
    prefix = runtime / ".venv"
    site = prefix / "Lib/site-packages"
    (site / module).mkdir(parents=True)
    (site / module / "__init__.py").write_bytes((package / "__init__.py").read_bytes())
    dist = site / (module + "-0.1.7rc2.dist-info")
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.4\nName: " + name + "\nVersion: 0.1.7rc2\n")
    (prefix / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sys.modules["sysconfig"], "get_path", lambda *_: str(site))
    from types import SimpleNamespace
    monkeypatch.setattr(sys.modules["importlib.util"], "find_spec", lambda name: SimpleNamespace(origin=str(site / name / "__init__.py")))
    receipt = {"schema_version": 1, "status": "ready", "payload_root": str(payload.resolve()),
               "package": H["source_identity"](payload), "peer": None}
    if usd:
        peer_root = site / "sca_physics"
        peer_root.mkdir()
        (peer_root / "__init__.py").write_text("PEER = 1\n")
        peer_dist = site / "sca_physics-0.1.7rc2.dist-info"
        peer_dist.mkdir()
        (peer_dist / "METADATA").write_text("Metadata-Version: 2.4\nName: sca-physics\nVersion: 0.1.7rc2\n")
        files = H["file_manifest"](peer_root)
        receipt["peer"] = {"name": "sca-physics", "version": "0.1.7rc2", "module": "sca_physics",
                           "files": files, "code_sha256": H["fingerprint"](files)}
    H["atomic_json"](runtime / H["RECEIPT"], receipt)
    return payload, runtime, site, receipt


def test_matching_bytes_need_no_business_import(tmp_path, monkeypatch):
    payload, runtime, _, _ = fixture_install(tmp_path, monkeypatch)
    result = H["guard"](payload, runtime)
    assert result["package"]["version"] == "0.1.7rc2"


def test_same_version_old_runtime_is_rejected(tmp_path, monkeypatch):
    payload, runtime, site, _ = fixture_install(tmp_path, monkeypatch)
    (site / "sca_physics/__init__.py").write_text("OLD = True\n")
    with pytest.raises(ValueError, match="runtime code differs"):
        H["guard"](payload, runtime)


def test_extra_deleted_module_is_rejected(tmp_path, monkeypatch):
    payload, runtime, site, _ = fixture_install(tmp_path, monkeypatch)
    (site / "sca_physics/stale.py").write_text("OLD = True\n")
    with pytest.raises(ValueError, match="extra="):
        H["guard"](payload, runtime)


def test_payload_update_requires_provision(tmp_path, monkeypatch):
    payload, runtime, _, _ = fixture_install(tmp_path, monkeypatch)
    (payload / "src/sca_physics/__init__.py").write_text("NEW = True\n")
    with pytest.raises(ValueError, match="changed since provision"):
        H["guard"](payload, runtime)


def test_missing_receipt_fails_closed(tmp_path, monkeypatch):
    payload, runtime, _, _ = fixture_install(tmp_path, monkeypatch)
    (runtime / H["RECEIPT"]).unlink()
    with pytest.raises(ValueError, match="receipt is missing"):
        H["guard"](payload, runtime)


def test_cache_is_ignored_but_dependency_change_is_not(tmp_path, monkeypatch):
    payload, runtime, site, _ = fixture_install(tmp_path, monkeypatch)
    cache = site / "sca_physics/__pycache__"
    cache.mkdir()
    (cache / "old.pyc").write_bytes(b"old")
    H["guard"](payload, runtime)
    with (payload / "pyproject.toml").open("a") as f:
        f.write('description = "metadata changed"\n')
    with pytest.raises(ValueError, match="changed since provision"):
        H["guard"](payload, runtime)


def test_wheel_identity_reads_bytes_without_import(tmp_path):
    wheel = tmp_path / "example.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("sca_physics/__init__.py", "raise RuntimeError('no import')\n")
        z.writestr("sca_physics-0.1.7rc2.dist-info/METADATA", "Name: sca-physics\nVersion: 0.1.7rc2\n")
    actual = H["wheel_identity"](wheel, "sca-physics")
    assert actual["version"] == "0.1.7rc2"
    assert actual["files"]["__init__.py"]


def test_usd_peer_same_version_stale_bytes_rejected(tmp_path, monkeypatch):
    payload, runtime, site, _ = fixture_install(tmp_path, monkeypatch, usd=True)
    H["guard"](payload, runtime)
    (site / "sca_physics/__init__.py").write_text("PEER = 'old'\n")
    with pytest.raises(ValueError, match="sca-physics runtime code differs"):
        H["guard"](payload, runtime)


@pytest.mark.parametrize("with_peer", [False, True])
def test_optional_physics_identity_supports_geometry_and_paired_modes(tmp_path, monkeypatch, with_peer):
    import shutil
    payload, runtime, site, receipt = fixture_install(tmp_path, monkeypatch, usd=True)
    project = payload / "pyproject.toml"
    project.write_text(project.read_text().replace(
        'dependencies = ["sca-physics==0.1.7rc2"]',
        'dependencies = []\n[project.optional-dependencies]\nphysics = ["sca-physics==0.1.7rc2"]'))
    receipt["package"] = H["source_identity"](payload)
    if not with_peer:
        shutil.rmtree(site / "sca_physics")
        shutil.rmtree(site / "sca_physics-0.1.7rc2.dist-info")
        receipt["peer"] = None
    H["atomic_json"](runtime / H["RECEIPT"], receipt)
    result = H["guard"](payload, runtime)
    assert ("peer" in result) == with_peer
    if not with_peer:
        (site / "sca_physics").mkdir()
        with pytest.raises(ValueError, match="Unreceipted optional"):
            H["guard"](payload, runtime)


D = runpy.run_path(str(ROOT / "scripts/pair_deploy.py"))


def make_pair(tmp_path):
    import argparse
    import shutil
    sources = tmp_path / "sources"
    home = tmp_path / "candidate/addons"
    private = tmp_path / "candidate/skills"
    global_skills = tmp_path / "global-skills"
    global_skills.mkdir()
    for name in ("sca-physics", "sca-usd-export"):
        source = sources / name
        module = name.replace("-", "_")
        (source / "src" / module).mkdir(parents=True)
        (source / "src" / module / "__init__.py").write_text("VALUE = 2\n")
        requirements = 'dependencies = ["sca-physics==0.1.7rc2"]\n' if name == "sca-usd-export" else 'dependencies = []\n'
        (source / "pyproject.toml").write_text('[project]\nname = "' + name + '"\nversion = "0.1.7rc2"\n' + requirements)
        (source / "sca-addon.toml").write_text('[addon]\nname = "' + name + '"\nversion = "0.1.7rc2"\n')
        (source / "skill").mkdir()
        (source / "skill/SKILL.md").write_text("new " + name)
        shutil.copytree(source, home / name)
        shutil.copytree(source / "skill", private / name)
        (global_skills / name).mkdir()
        (global_skills / name / "SKILL.md").write_text("old " + name)
    records = {}
    for name in ("sca-physics", "sca-usd-export"):
        runtime = home.parent / "runtimes" / name
        (runtime / ".venv/Scripts").mkdir(parents=True)
        (runtime / ".venv/Scripts/python.exe").write_bytes(b"fake")
        (runtime / ".venv/pyvenv.cfg").write_text("include-system-site-packages = false\n")
        site = runtime / ".venv/Lib/site-packages"
        package_names = ["sca-physics", "sca-usd-export"] if name == "sca-usd-export" else ["sca-physics"]
        for package_name in package_names:
            module = package_name.replace("-", "_")
            shutil.copytree(sources / package_name / "src" / module, site / module)
            dist = site / (module + "-0.1.7rc2.dist-info")
            dist.mkdir()
            (dist / "METADATA").write_text("Name: " + package_name + "\nVersion: 0.1.7rc2\n")
        records[name] = {"version": "0.1.7rc2", "source": {"kind": "local", "path": str(sources / name)},
                         "runtime_dir": str(runtime), "skill_install": {"dir_name": name, "skills_dir": str(global_skills)}}
    H["atomic_json"](home / "registry.json", {"addons": records})
    old_home = tmp_path / "old/addons"
    old_home.mkdir(parents=True)
    old_records = json.loads(json.dumps(records))
    for name in old_records:
        old_records[name]["version"] = "0.1.7rc1"
        shutil.copytree(home / name, old_home / name)
        old_runtime = old_home.parent / "runtimes" / name
        shutil.copytree(home.parent / "runtimes" / name, old_runtime)
        old_records[name]["runtime_dir"] = str(old_runtime)
    H["atomic_json"](old_home / "registry.json", {"addons": old_records})
    legacy_home = tmp_path / "legacy/addons"
    for name in records:
        shutil.copytree(old_home / name, legacy_home / name)
    H["atomic_json"](legacy_home / "registry.json", {"addons": old_records})
    config = tmp_path / "config.toml"
    config.write_text("addon_home = " + json.dumps(str(old_home)) + "\nskills_dir = " + json.dumps(str(global_skills)) + "\n")
    receipt = tmp_path / "stage.json"
    H["atomic_json"](receipt, {"status": "verified", "source_manifests": {n:H["payload_manifest"](sources / n) for n in records}})
    args = argparse.Namespace(physics_source=sources / "sca-physics", usd_source=sources / "sca-usd-export",
        home=home, skills_dir=private, sca_python=Path(sys.executable), bootstrap_python=Path(sys.executable),
        state_dir=tmp_path / "activation", config=config, global_skills_dir=global_skills,
        profiles=None, channel="stable", stage_receipt=receipt, allow_downgrade=False, legacy_verification=False,
        retained_installation=[(str(legacy_home), str(tmp_path / "legacy/skills"))])
    args.expected_current = D["current_identity"](config)["id"]
    return args, legacy_home


def test_activation_detaches_before_sdk_update_and_rolls_back(tmp_path, monkeypatch):
    args, legacy = make_pair(tmp_path)
    before_config = args.config.read_bytes()
    before_global = {p.relative_to(args.global_skills_dir).as_posix():p.read_bytes() for p in args.global_skills_dir.rglob("*") if p.is_file()}
    calls = []
    def command(args, words, log=None, **kwargs):
        if words[0] == "update":
            # This is the critical order: even the FIRST update sees private targets.
            row = json.loads((legacy / "registry.json").read_text())
            assert all(x["skill_install"]["skills_dir"] != str(args.global_skills_dir) for x in row["addons"].values())
            old_home = Path(D["tomllib"].loads(args.config.read_text())["addon_home"])
            old = json.loads((old_home / "registry.json").read_text())
            assert all(x["skill_install"]["skills_dir"] != str(args.global_skills_dir) for x in old["addons"].values())
        calls.append(words)
        if kwargs.get("default"):
            raise RuntimeError("injected failure after actual-default publication")
        return b"{}"
    monkeypatch.setitem(D["activate"].__globals__, "cli", command)
    with pytest.raises(RuntimeError, match="injected failure"):
        D["activate"](args)
    assert args.config.read_bytes() == before_config
    after = {p.relative_to(args.global_skills_dir).as_posix():p.read_bytes() for p in args.global_skills_dir.rglob("*") if p.is_file()}
    assert after == before_global
    assert json.loads((args.state_dir / "rollback-result.json").read_text())["restored"] is True
    assert calls[0][0] == "update"


def test_stale_expected_current_never_mutates(tmp_path):
    args, _ = make_pair(tmp_path)
    args.expected_current = "stale"
    with pytest.raises(ValueError, match="identity changed"):
        D["activate"](args)
    assert not args.state_dir.exists()


def test_explicit_rollback_cannot_bypass_current_identity_gate(tmp_path):
    with pytest.raises(ValueError, match="requires --expected-current"):
        D["main"](["rollback", "--apply", "--rollback-manifest", str(tmp_path / "not-read.json")])


def test_legacy_range_is_only_an_explicit_recovery_exception(tmp_path):
    args, _ = make_pair(tmp_path)
    project = args.usd_source / "pyproject.toml"
    project.write_text(project.read_text().replace("sca-physics==0.1.7rc2", "sca-physics>=0.1.7rc1,<0.2"))
    with pytest.raises(ValueError, match="must pin"):
        D["validate_sources"](args)
    args.legacy_verification = True
    D["validate_sources"](args)
