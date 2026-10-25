"""Explicit paired add-on deployment. Plans are read-only; mutations require --apply."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tomllib
import uuid
import zipfile

H = runpy.run_path(str(Path(__file__).with_name("install_identity.py")))
PLUGINS = ("sca-physics", "sca-usd-export")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    H["atomic_json"](path, value)


def absolute(value):
    return Path(value).expanduser().resolve()


def source_paths(args):
    return {PLUGINS[0]: absolute(args.physics_source), PLUGINS[1]: absolute(args.usd_source)}


def selected_environment(args, profile=None):
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "SCA_ADDON_HOME", "SCA_SKILLS_DIR", "SCA_ADDON_DIR", "SCA_RUNTIME_DIR"):
        env.pop(key, None)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
               SCA_PHYSICS_SOURCE=str(absolute(args.physics_source)),
               SCA_PHYSICS_BOOTSTRAP_PYTHON=str(absolute(args.bootstrap_python)),
               SCA_USD_BOOTSTRAP_PYTHON=str(absolute(args.bootstrap_python)))
    if profile:
        env.update(USERPROFILE=str(profile), APPDATA=str(profile / "AppData/Roaming"),
                   LOCALAPPDATA=str(profile / "AppData/Local"))
    return env


def cli(args, words, log=None, *, profile=None, default=False):
    command = [str(absolute(args.sca_python)), "-I", "-B", "-m", "simplecadapi.cli", "addon", *words]
    if not default:
        # Position flags before the addon name/command tail.
        command[7:7] = ["--home", str(absolute(args.home)), "--skills-dir", str(absolute(args.skills_dir))]
    result = subprocess.run(command, env=selected_environment(args, profile), capture_output=True)
    if log:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.with_suffix(".stdout.log").write_bytes(result.stdout)
        log.with_suffix(".stderr.log").write_bytes(result.stderr)
        write(log.with_suffix(".command.json"), {"argv": command, "exit_code": result.returncode})
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command}; {result.stderr.decode('utf-8', errors='replace')[-1500:]}")
    return result.stdout


def validate_sources(args):
    sources = source_paths(args)
    identities = {}
    for name, path in sources.items():
        H["assert_plain_tree"](path)
        identities[name] = H["source_identity"](path)
        if identities[name]["name"] != name or path.name != name:
            raise ValueError(f"Source must be the correctly named {name} repository")
        descriptor = tomllib.loads((path / "sca-addon.toml").read_text(encoding="utf-8-sig"))["addon"]
        if descriptor["name"] != name or descriptor["version"] != identities[name]["version"]:
            raise ValueError("Descriptor/project identity mismatch")
    pins = [x.split("==", 1)[1] for x in H["physics_requirements"](identities[PLUGINS[1]]) if x.startswith("sca-physics==")]
    if len(pins) != 1 or pins[0] != identities[PLUGINS[0]]["version"]:
        legacy = args.legacy_verification and not (sources[PLUGINS[1]] / "scripts/install_identity.py").is_file()
        requirements = [x[len("sca-physics"):].strip() for x in H["physics_requirements"](identities[PLUGINS[1]])]
        if not legacy or len(requirements) != 1 or not satisfies(identities[PLUGINS[0]]["version"], requirements[0]):
            raise ValueError("USD must pin this exact physics version; explicit historical recovery must satisfy its recorded range")
    return identities


def static_install(args, *, skills=None, require_source=True):
    identities = validate_sources(args)
    home = absolute(args.home)
    skill_root = absolute(skills or args.skills_dir)
    registry = read(home / "registry.json")
    result = {}
    for name, source in source_paths(args).items():
        payload = home / name
        H["assert_plain_tree"](payload)
        if H["payload_manifest"](source) != H["payload_manifest"](payload):
            raise ValueError(f"{name}: source and complete payload differ")
        record = registry["addons"][name]
        if record["version"] != identities[name]["version"]:
            raise ValueError("Registry version differs from source")
        if require_source and (record["source"].get("kind") != "local" or absolute(record["source"]["path"]) != source):
            raise ValueError("Registry does not point at the explicitly selected source")
        runtime = absolute(record["runtime_dir"])
        if runtime != (home.parent / "runtimes" / name).resolve():
            raise ValueError("Registry runtime escapes this installation")
        python = runtime / ".venv/Scripts/python.exe"
        if not python.is_file():
            raise ValueError(f"Missing interpreter: {python}")
        config = (runtime / ".venv/pyvenv.cfg").read_text().lower()
        if "include-system-site-packages = false" not in config:
            raise ValueError("Runtime inherits host packages")
        site = runtime / ".venv/Lib/site-packages"
        installed = H["installed_identity"](identities[name], site)
        expected_skill = H["file_manifest"](payload / "skill")
        if H["file_manifest"](skill_root / name) != expected_skill:
            raise ValueError(f"{name}: incomplete or different skill/reference tree")
        row = {"version": identities[name]["version"], "source": str(source), "runtime": str(runtime),
               "package": identities[name], "payload_files": H["payload_manifest"](payload),
               "skill_files": expected_skill, "installed": installed}
        if name == "sca-usd-export":
            peer = identities["sca-physics"]
            row["physics"] = H["installed_identity"](peer, site)
        # New guarded payloads require their own successful provision receipt.
        if (payload / "scripts/install_identity.py").is_file():
            receipt = read(runtime / H["RECEIPT"])
            if receipt.get("status") != "ready" or receipt["package"] != identities[name]:
                raise ValueError("Missing/stale provision receipt")
            if name == "sca-usd-export" and receipt.get("peer", {}).get("files") != identities["sca-physics"]["files"]:
                raise ValueError("USD receipt physics source differs from selected pair")
            row["receipt_sha256"] = H["digest"]((runtime / H["RECEIPT"]).read_bytes())
        result[name] = row
    return result


def current_identity(config):
    configuration = tomllib.loads(Path(config).read_text(encoding="utf-8-sig"))
    home = absolute(configuration["addon_home"])
    skills = absolute(configuration["skills_dir"])
    registry = read(home / "registry.json")
    pair = {}
    for name in PLUGINS:
        record = registry["addons"][name]
        runtime = absolute(record["runtime_dir"])
        package_root = runtime / ".venv/Lib/site-packages" / name.replace("-", "_")
        pair[name] = {"version": record["version"], "source": record["source"],
                      "runtime_dir": str(runtime), "skill_install": record["skill_install"],
                      "payload": H["payload_manifest"](home / name),
                      "runtime_files": H["file_manifest"](package_root),
                      "skill": H["file_manifest"](skills / name)}
    return {"id": H["fingerprint"]({"home": str(home), "skills": str(skills), "pair": pair}),
            "home": str(home), "skills": str(skills), "pair": pair}


def version_key(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?", value)
    if not match:
        raise ValueError(f"Unsupported version comparison: {value}")
    numbers = tuple(map(int, match[1].split(".")))
    return numbers + (0,) * (3 - len(numbers)) + ({"a": 0, "b": 1, "rc": 2, None: 3}[match[2]], int(match[3] or 0))


def satisfies(version, expression):
    value = version_key(version)
    for clause in expression.split(","):
        match = re.fullmatch(r"\s*(==|!=|>=|<=|>|<)\s*([^\s]+)\s*", clause)
        if not match:
            return False
        other = version_key(match[2])
        if not {"==": value == other, "!=": value != other, ">=": value >= other,
                "<=": value <= other, ">": value > other, "<": value < other}[match[1]]:
            return False
    return True


def replace_config(original, home, skills):
    text = original.decode("utf-8-sig")
    values = {"addon_home": str(home), "skills_dir": str(skills)}
    lines, seen, top = [], set(), True
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            top = False
        match = re.match(r"\s*(addon_home|skills_dir)\s*=", line) if top else None
        if match:
            key = match[1]
            lines.append(key + " = " + json.dumps(values[key]) + "\n")
            seen.add(key)
        else:
            lines.append(line)
    result = "".join(key + " = " + json.dumps(values[key]) + "\n" for key in values if key not in seen) + "".join(lines)
    parsed = tomllib.loads(result)
    if any(parsed[key] != value for key, value in values.items()):
        raise ValueError("Cannot safely rewrite selected config paths")
    return result.encode("utf-8")


def atomic_bytes(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_bytes(data)
    os.replace(temporary, path)


def retained(args):
    rows = [(absolute(h), absolute(s)) for h, s in args.retained_installation]
    if args.config:
        active_home = absolute(current_identity(args.config)["home"])
        if active_home != absolute(args.home) and active_home not in {h for h, _ in rows}:
            rows.append((active_home, active_home.parent / "skills"))
    # The selected installation must also be detached before normal update.
    rows.append((absolute(args.home), absolute(args.skills_dir)))
    unique = {}
    for home, skills in rows:
        if home in unique and unique[home] != skills:
            raise ValueError("Conflicting private skill roots for one home")
        if args.global_skills_dir and skills == absolute(args.global_skills_dir):
            raise ValueError("Retained/isolated skills must be private")
        unique[home] = skills
    return list(unique.items())


def backup_state(args, folder):
    folder.mkdir(parents=True, exist_ok=False)
    files = [absolute(args.config)]
    if args.profiles:
        files.append(absolute(args.profiles))
    homes = {absolute(args.home), absolute(current_identity(args.config)["home"])}
    homes.update(home for home, _ in retained(args))
    files.extend(home / "registry.json" for home in homes)
    records = []
    for i, original in enumerate(dict.fromkeys(files)):
        target = folder / f"file-{i:03}.bin"
        target.write_bytes(original.read_bytes())
        records.append({"original": str(original), "backup": str(target), "sha256": H["digest"](target.read_bytes())})
    global_root = absolute(args.global_skills_dir)
    skill_files = {}
    archive = folder / "global-skills.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
        for name in PLUGINS:
            directory = global_root / name
            H["assert_plain_tree"](directory)
            for rel, digest in H["file_manifest"](directory).items():
                member = name + "/" + rel
                z.write(directory / rel, member)
                skill_files[member] = digest
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None or {n:H["digest"](z.read(n)) for n in z.namelist()} != skill_files:
            raise ValueError("Rollback skill archive verification failed")
    manifest = {"files": records, "global_root": str(global_root), "skill_files": skill_files,
                "archive": str(archive), "archive_sha256": H["digest"](archive.read_bytes()),
                "runtime_policy": "previous runtimes remain in place"}
    write(folder / "manifest.json", manifest)
    return folder / "manifest.json"


@contextlib.contextmanager
def global_lock(global_root):
    path = Path(global_root) / ".sca-pair-deploy.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        path.unlink()


def safe_skill_target(root, name):
    root = absolute(root)
    target = root / name
    if name not in PLUGINS or target.resolve().parent != root:
        raise ValueError("Unsafe skill replacement target")
    if target.exists():
        H["assert_plain_tree"](target)
    return target


def publish_skill(source, root, name):
    root = absolute(root)
    root.mkdir(parents=True, exist_ok=True)
    target = safe_skill_target(root, name)
    stage = root / ("." + name + ".stage-" + uuid.uuid4().hex)
    previous = root / ("." + name + ".previous-" + uuid.uuid4().hex)
    H["assert_plain_tree"](source)
    shutil.copytree(source, stage)
    if H["file_manifest"](stage) != H["file_manifest"](source):
        raise ValueError("Skill staging bytes differ")
    if target.exists():
        target.rename(previous)
    try:
        stage.rename(target)
    except BaseException:
        if previous.exists():
            previous.rename(target)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def restore(manifest_path):
    manifest = read(manifest_path)
    archive = Path(manifest["archive"])
    if H["digest"](archive.read_bytes()) != manifest["archive_sha256"]:
        raise ValueError("Rollback archive changed")
    for record in manifest["files"]:
        if H["digest"](Path(record["backup"]).read_bytes()) != record["sha256"]:
            raise ValueError("Rollback backup changed")
    root = absolute(manifest["global_root"])
    with zipfile.ZipFile(archive) as z:
        if {n:H["digest"](z.read(n)) for n in z.namelist()} != manifest["skill_files"]:
            raise ValueError("Rollback members differ")
        for name in PLUGINS:
            stage = Path(tempfile_directory(root))
            try:
                for member in manifest["skill_files"]:
                    if member.split("/")[0] != name:
                        continue
                    relative = Path(*member.split("/")[1:])
                    target = stage / relative
                    if not target.resolve().is_relative_to(stage.resolve()):
                        raise ValueError("Unsafe rollback member")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(member))
                publish_skill(stage, root, name)
            finally:
                shutil.rmtree(stage)
    for record in manifest["files"]:
        atomic_bytes(record["original"], Path(record["backup"]).read_bytes())


def tempfile_directory(root):
    import tempfile
    return tempfile.mkdtemp(prefix=".sca-restore-", dir=str(root))


def stage(args):
    validate_sources(args)
    state = absolute(args.state_dir)
    state.mkdir(parents=True, exist_ok=False)
    profile = state / "profile"
    profile.mkdir()
    env = selected_environment(args, profile)
    init = [str(absolute(args.sca_python)), "-I", "-B", "-m", "simplecadapi.cli", "init",
            "--home", str(absolute(args.home)), "--skills-dir", str(absolute(args.skills_dir)), "--no-shell"]
    subprocess.run(init, env=env, check=True)
    source_before = {n:H["payload_manifest"](p) for n,p in source_paths(args).items()}
    registry_path = absolute(args.home) / "registry.json"
    for name, source in source_paths(args).items():
        present = name in read(registry_path)["addons"]
        if present:
            # Before update, existing target must already be private.
            record = read(registry_path)["addons"][name]
            if absolute(record["skill_install"]["skills_dir"]) != absolute(args.skills_dir):
                raise ValueError("Stage refuses an existing non-private skill target; detach using protected activation")
            cli(args, ["update", name], state / ("update-" + name), profile=profile)
        else:
            cli(args, ["add", str(source)], state / ("add-" + name), profile=profile)
        cli(args, ["use", name, "provision"], state / ("provision-" + name), profile=profile)
        cli(args, ["check", name], state / ("check-" + name), profile=profile)
        if read(registry_path)["addons"][name]["runtime_check"]["passed"] is not True:
            raise ValueError("Runtime probe failed")
    result = static_install(args)
    if source_before != {n:H["payload_manifest"](p) for n,p in source_paths(args).items()}:
        raise ValueError("Source changed while staging")
    write(state / "deployment.json", {"status": "verified", "id": H["fingerprint"](result), "pair": result,
                                      "source_manifests": source_before, "scope": "installation/code identity, no simulation"})
    print(json.dumps({"passed": True, "deployment": str(state / "deployment.json"),
                      "id": H["fingerprint"](result)}))


def activate(args):
    if not args.config or not args.global_skills_dir or not args.expected_current:
        raise ValueError("Activation requires --config, --global-skills-dir and --expected-current")
    before = current_identity(args.config)
    if before["id"] != args.expected_current:
        raise ValueError("Current installation identity changed; create a new reviewed plan")
    identities = validate_sources(args)
    downgrade = any(version_key(identities[n]["version"]) < version_key(before["pair"][n]["version"]) for n in PLUGINS)
    if downgrade and not args.allow_downgrade:
        raise ValueError("Downgrade requires --allow-downgrade and the expected current identity")
    legacy = args.legacy_verification and not (absolute(args.usd_source) / "scripts/install_identity.py").is_file()
    if not args.stage_receipt and not legacy:
        raise ValueError("Activation requires --stage-receipt; historical recovery also requires --legacy-verification")
    if args.stage_receipt:
        staged = read(args.stage_receipt)
        if staged.get("status") != "verified" or staged["source_manifests"] != {n:H["payload_manifest"](p) for n,p in source_paths(args).items()}:
            raise ValueError("Stage receipt/source manifest mismatch")
    state = absolute(args.state_dir)
    state.mkdir(parents=True, exist_ok=False)
    with global_lock(absolute(args.global_skills_dir)):
        if current_identity(args.config)["id"] != before["id"]:
            raise ValueError("Current installation changed before activation lock")
        backup = backup_state(args, state / "rollback")
        try:
            # Rebind BEFORE calling SDK update, which uses the old record first.
            for home, private in retained(args):
                registry = read(home / "registry.json")
                for name in PLUGINS:
                    record = registry.get("addons", {}).get(name)
                    if record:
                        if home != absolute(args.home):
                            source_skill = home / name / "skill"
                            target_skill = private / name
                            if not target_skill.exists():
                                publish_skill(source_skill, private, name)
                            elif H["file_manifest"](target_skill) != H["file_manifest"](source_skill):
                                raise ValueError("Retained private skill differs from its payload; resolve explicitly before activation")
                        record["skill_install"]["skills_dir"] = str(private)
                write(home / "registry.json", registry)
            for name in PLUGINS:
                cli(args, ["update", name], state / ("private-update-" + name))
            verified = static_install(args)
            for name in PLUGINS:
                cli(args, ["use", name, "check"], state / ("private-use-" + name))
            # Selected runtime is already verified; publish only its final skills.
            for name in PLUGINS:
                publish_skill(absolute(args.home) / name / "skill", args.global_skills_dir, name)
            registry = read(absolute(args.home) / "registry.json")
            for name in PLUGINS:
                registry["addons"][name]["skill_install"]["skills_dir"] = str(absolute(args.global_skills_dir))
            write(absolute(args.home) / "registry.json", registry)
            atomic_bytes(args.config, replace_config(Path(args.config).read_bytes(), absolute(args.home), absolute(args.global_skills_dir)))
            if args.profiles:
                profiles = read(args.profiles)
                previous = profiles["channels"][args.channel]
                profiles["retained_previous_stable"] = {"profile": previous, "rollback_manifest": str(backup)}
                profiles["channels"][args.channel] = {
                    "addon_home": str(absolute(args.home)), "skills_dir": str(absolute(args.skills_dir)),
                    "physics_source": str(absolute(args.physics_source)), "usd_source": str(absolute(args.usd_source)),
                    "pythons": {key: str(absolute(args.home).parent / "runtimes" / name / ".venv/Scripts/python.exe")
                                for key,name in [("physics", PLUGINS[0]), ("usd", PLUGINS[1])]},
                    "output_root": str(state / "outputs")}
                write(args.profiles, profiles)
            for name in PLUGINS:
                cli(args, ["use", name, "check"], state / ("actual-default-" + name), default=True)
            after = current_identity(args.config)
            if absolute(after["home"]) != absolute(args.home) or absolute(after["skills"]) != absolute(args.global_skills_dir):
                raise ValueError("Actual default paths differ")
            static_install(args, skills=args.global_skills_dir)
            write(state / "activation.json", {"passed": True, "before_id": before["id"], "after_id": after["id"],
                                             "rollback_manifest": str(backup), "pair": verified,
                                             "scope": "local deployment/code identity, no simulation or GitHub-install claim"})
            print(json.dumps({"passed": True, "identity": after["id"], "rollback_manifest": str(backup)}))
        except BaseException:
            restore(backup)
            write(state / "rollback-result.json", {"restored": True, "previous_runtime_untouched": True})
            raise


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["plan", "stage", "verify", "activate", "rollback", "current-identity"])
    p.add_argument("--apply", action="store_true")
    for name in ["sca-python", "bootstrap-python", "physics-source", "usd-source", "home", "skills-dir", "state-dir",
                 "global-skills-dir", "config", "profiles", "stage-receipt", "rollback-manifest"]:
        p.add_argument("--" + name, type=Path)
    p.add_argument("--expected-current")
    p.add_argument("--allow-downgrade", action="store_true")
    p.add_argument("--legacy-verification", action="store_true")
    p.add_argument("--channel", choices=["stable", "experimental"], default="stable")
    p.add_argument("--retained-installation", nargs=2, action="append", default=[], metavar=("HOME", "PRIVATE_SKILLS"))
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.action == "current-identity":
        if not args.config:
            raise ValueError("Specify --config")
        print(json.dumps(current_identity(args.config)))
        return 0
    if args.action == "rollback":
        if not args.rollback_manifest:
            raise ValueError("Specify --rollback-manifest")
        if args.apply:
            if not args.expected_current or not args.allow_downgrade:
                raise ValueError("Explicit rollback requires --expected-current and --allow-downgrade")
            manifest = read(args.rollback_manifest)
            config = args.config or Path(manifest["files"][0]["original"])
            if current_identity(config)["id"] != args.expected_current:
                raise ValueError("Current identity changed; refusing stale rollback")
            with global_lock(absolute(manifest["global_root"])):
                if current_identity(config)["id"] != args.expected_current:
                    raise ValueError("Current identity changed before rollback lock")
                restore(args.rollback_manifest)
        print(json.dumps({"rollback_manifest": str(args.rollback_manifest), "applied": args.apply}))
        return 0
    required = ["sca_python", "bootstrap_python", "physics_source", "usd_source", "home", "skills_dir", "state_dir"]
    if any(getattr(args, name) is None for name in required):
        raise ValueError("Explicit host/base Python, both sources, home, skills-dir and state-dir are required")
    if args.action == "verify":
        result = static_install(args)
        print(json.dumps({"passed": True, "id": H["fingerprint"](result), "pair": result}))
        return 0
    if args.action == "plan" or not args.apply:
        print(json.dumps({"action": args.action, "apply": False, "sources": {n:str(p) for n,p in source_paths(args).items()},
                          "home": str(absolute(args.home)), "skills_dir": str(absolute(args.skills_dir)),
                          "versions": {n:x["version"] for n,x in validate_sources(args).items()},
                          "current_identity": current_identity(args.config)["id"] if args.config else None}))
        return 0
    {"stage": stage, "activate": activate}[args.action](args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError, KeyError) as exc:
        print(f"pair-deploy: {exc}", file=sys.stderr)
        raise SystemExit(2)
