"""Build and verify add-on code identities without importing add-on business code."""
from __future__ import annotations
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig
import tomllib
import uuid
import zipfile

SCHEMA = 1
RECEIPT = "install-receipt.json"
EXCLUDED = {".git", "__pycache__", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist", "runtimes"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode())


def assert_plain_tree(root):
    root = Path(root)
    if root.is_symlink() or getattr(root.lstat(), "st_file_attributes", 0) & 1024:
        raise ValueError(f"Reparse/symlink directory is unsupported: {root}")
    for path in root.rglob("*"):
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 1024:
            raise ValueError(f"Reparse/symlink path is unsupported: {path}")


def file_manifest(root, *, source=False):
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"Missing package directory: {root}")
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in EXCLUDED for part in rel.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError(f"Symlink is unsupported: {path}")
        if path.is_file():
            files[rel.as_posix()] = digest(path.read_bytes())
    return files


def project(payload):
    return tomllib.loads((Path(payload) / "pyproject.toml").read_text(encoding="utf-8-sig"))["project"]


def source_identity(payload):
    payload = Path(payload).resolve()
    info = project(payload)
    module = info["name"].replace("-", "_")
    files = file_manifest(payload / "src" / module)
    if not files:
        raise ValueError(f"Empty source package: {module}")
    identity = {"name": info["name"], "version": info["version"], "module": module,
            "files": files, "code_sha256": fingerprint(files),
            "requirements": info.get("dependencies", []),
            "pyproject_sha256": digest((payload / "pyproject.toml").read_bytes())}
    if info["name"] == "sca-usd-export" and "optional-dependencies" in info:
        identity["optional_requirements"] = info["optional-dependencies"]
    return identity


def physics_requirements(identity):
    """Read current optional pin and retained legacy mandatory declarations."""
    return [x for x in identity["requirements"] + identity.get("optional_requirements", {}).get("physics", [])
            if x.startswith("sca-physics")]


def payload_manifest(payload):
    return file_manifest(payload, source=True)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def wheel_identity(wheel, expected_name=None):
    wheel = Path(wheel).resolve()
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:
            raise ValueError(f"Corrupt wheel: {wheel}")
        metadata_paths = [x for x in archive.namelist() if x.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise ValueError("Wheel must have exactly one METADATA")
        metadata = archive.read(metadata_paths[0]).decode("utf-8")
        values = {}
        for line in metadata.splitlines():
            if line.startswith(("Name: ", "Version: ")):
                key, value = line.split(": ", 1)
                values[key] = value
        name = values.get("Name")
        if expected_name and name != expected_name:
            raise ValueError(f"Expected {expected_name} wheel, received {name}")
        module = name.replace("-", "_")
        prefix = module + "/"
        files = {x[len(prefix):]: digest(archive.read(x)) for x in archive.namelist()
                 if x.startswith(prefix) and not x.endswith("/") and "__pycache__" not in x.split("/")
                 and not x.endswith((".pyc", ".pyo"))}
    if not files:
        raise ValueError(f"Wheel has no {module} package")
    return {"name": name, "version": values["Version"], "module": module,
            "files": files, "code_sha256": fingerprint(files),
            "wheel_sha256": digest(wheel.read_bytes()), "wheel": str(wheel)}


def installed_identity(expected, site=None):
    check_origin = site is None
    site = Path(site or sysconfig.get_path("purelib")).resolve()
    module_root = site / expected["module"]
    if check_origin:
        spec = importlib.util.find_spec(expected["module"])
        if not spec or not spec.origin or Path(spec.origin).resolve() != (module_root / "__init__.py").resolve():
            raise ValueError(f"{expected['name']} would import from a different package; run provision")
    actual = file_manifest(module_root)
    if actual != expected["files"]:
        missing = sorted(set(expected["files"]) - set(actual))
        extra = sorted(set(actual) - set(expected["files"]))
        different = sorted(x for x in set(actual) & set(expected["files"]) if actual[x] != expected["files"][x])
        raise ValueError(f"{expected['name']} runtime code differs from expected source: missing={missing}, extra={extra}, different={different}. Run provision for this explicit home/skills installation.")
    distributions = list(importlib.metadata.distributions(path=[str(site)]))
    matching = [d for d in distributions if d.metadata["Name"] == expected["name"]]
    if len(matching) != 1 or matching[0].version != expected["version"]:
        raise ValueError(f"{expected['name']} installed distribution/version is not the expected {expected['version']}")
    return {"module_root": str(module_root), "version": matching[0].version,
            "code_sha256": fingerprint(actual), "files": actual}


def guard(payload, runtime, receipt=None):
    payload, runtime = Path(payload).resolve(), Path(runtime).resolve()
    if Path(sys.prefix).resolve() != (runtime / ".venv").resolve() or sys.prefix == sys.base_prefix:
        raise ValueError("Runtime identity requires this installation's own .venv")
    if "include-system-site-packages = false" not in (Path(sys.prefix) / "pyvenv.cfg").read_text().lower():
        raise ValueError("Runtime must not inherit system site-packages")
    if receipt is None:
        path = runtime / RECEIPT
        if not path.is_file():
            raise ValueError("Runtime identity receipt is missing. Run provision for this explicit home/skills installation.")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("status") != "ready":
            raise ValueError("Runtime identity receipt is not ready; run provision")
    expected = source_identity(payload)
    if receipt.get("schema_version") != SCHEMA or receipt.get("package") != expected:
        raise ValueError("Payload code/dependency identity changed since provision. Run provision for this explicit home/skills installation.")
    if Path(receipt["payload_root"]).resolve() != payload:
        raise ValueError("Runtime receipt belongs to a different payload root; run provision")
    result = {"package": installed_identity(expected)}
    if expected["name"] == "sca-usd-export":
        peer = receipt.get("peer")
        pins = [x.split("==", 1)[1] for x in physics_requirements(expected) if x.startswith("sca-physics==")]
        mandatory = any(x.startswith("sca-physics") for x in expected["requirements"])
        if (peer or mandatory) and (not peer or peer.get("name") != "sca-physics" or len(pins) != 1 or peer["version"] != pins[0]):
            raise ValueError("USD requires an explicit matching physics artifact identity; run provision with SCA_PHYSICS_SOURCE")
        if peer:
            result["peer"] = installed_identity(peer)
        else:
            site = Path(sysconfig.get_path("purelib")).resolve()
            if (site / "sca_physics").exists() or any(d.metadata["Name"] == "sca-physics"
                    for d in importlib.metadata.distributions(path=[str(site)])):
                raise ValueError("Unreceipted optional sca-physics is installed; run provision with SCA_PHYSICS_SOURCE or choose a fresh geometry runtime")
    return result


def build_wheel(source, output, environment, python=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    before = source_identity(source)
    complete_before = payload_manifest(source)
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run([str(python or sys.executable), "-I", "-B", "-m", "pip", "--disable-pip-version-check",
                    "wheel", "--no-deps", "--wheel-dir", str(output), str(source)], env=environment, check=True)
    wheels = list(output.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Build did not produce exactly one wheel")
    identity = wheel_identity(wheels[0], before["name"])
    if before["files"] != identity["files"] or before["version"] != identity["version"]:
        raise ValueError("Built wheel differs from selected source")
    if source_identity(source) != before or payload_manifest(source) != complete_before:
        raise ValueError("Source changed while building; do not activate this build")
    identity["source_root"] = str(source)
    identity["source_files"] = complete_before
    return wheels[0], identity


def provision(payload, runtime):
    payload, runtime = Path(payload).resolve(), Path(runtime).resolve()
    expected = source_identity(payload)
    assert_plain_tree(payload)
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / ".venv"
    python = target / "Scripts/python.exe"
    if (sys.version_info[:2] != (3, 12) or sys.implementation.name != "cpython"
            or sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}):
        raise ValueError("Provision requires CPython 3.12 on Windows x86-64")
    if target.exists() and not python.is_file():
        raise ValueError("Incomplete .venv; choose a fresh runtime, no existing files were deleted")
    if not target.exists():
        import venv
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(target)
    if "include-system-site-packages = false" not in (target / "pyvenv.cfg").read_text().lower():
        raise ValueError("Runtime inherits system packages")
    cache = runtime / "cache"
    temporary = cache / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PIP_CACHE_DIR=str(cache / "pip"), TMP=str(temporary),
                       TEMP=str(temporary), PYTHONDONTWRITEBYTECODE="1",
                       SCA_RUNTIME_DIR=str(runtime), SCA_ADDON_DIR=str(payload))
    for key in ("PYTHONPATH", "PYTHONHOME"):
        environment.pop(key, None)
    wheelhouse = environment.get("SCA_ADDON_WHEELHOUSE") or environment.get("SCA_USD_WHEELHOUSE")
    if wheelhouse:
        directory = Path(wheelhouse).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError("Explicit wheelhouse does not exist")
        environment["PIP_FIND_LINKS"] = directory.as_uri() + " " + environment.get("PIP_FIND_LINKS", "")
    build = runtime / "builds" / uuid.uuid4().hex
    own_wheel, artifact = build_wheel(payload, build / "own", environment, python)
    wheels = [own_wheel]
    peer = None
    if expected["name"] == "sca-usd-export":
        value = environment.get("SCA_PHYSICS_SOURCE")
        mandatory = any(x.startswith("sca-physics") for x in expected["requirements"])
        if not value and mandatory:
            raise ValueError("Set SCA_PHYSICS_SOURCE to the explicit matching physics repository or wheel")
    else:
        value = None
    if value:
        source = Path(value).expanduser().resolve()
        if source.is_dir():
            peer_wheel, peer = build_wheel(source, build / "physics", environment, python)
        elif source.is_file():
            peer_wheel, peer = source, wheel_identity(source, "sca-physics")
        else:
            raise ValueError("SCA_PHYSICS_SOURCE does not exist")
        pins = [x.split("==", 1)[1] for x in physics_requirements(expected) if x.startswith("sca-physics==")]
        if peer["name"] != "sca-physics" or len(pins) != 1 or peer["version"] != pins[0]:
            raise ValueError("Physics artifact does not match this USD release")
        wheels.insert(0, peer_wheel)
    inputs = list(map(str, wheels))
    if expected["name"] == "sca-usd-export" and environment.get("SCA_USD_WHEELHOUSE"):
        wheelhouse = Path(environment["SCA_USD_WHEELHOUSE"]).expanduser().resolve()
        extra = sorted(wheelhouse.glob("*.whl")) if wheelhouse.is_dir() else []
        if not extra:
            raise ValueError("SCA_USD_WHEELHOUSE must contain wheel files")
        # Explicit wheel inputs retain the existing bootstrap behavior.
        inputs.extend(str(p) for p in extra if not p.name.startswith(("sca_physics-", "sca_usd_export-")))
    def run(args):
        subprocess.run([str(python), "-I", "-B", "-m", "pip", "--disable-pip-version-check", *args],
                       env=environment, check=True)
    run(["install", "--upgrade", *inputs])
    run(["install", "--force-reinstall", "--no-deps", *map(str, wheels)])
    run(["check"])
    receipt = {"schema_version": SCHEMA, "status": "ready", "payload_root": str(payload),
               "package": expected, "artifact": artifact, "peer": peer}
    candidate = build / "candidate-receipt.json"
    atomic_json(candidate, receipt)
    # Verify from the actual venv, before committing its readiness receipt.
    code = ("import runpy,sys,json; h=runpy.run_path(sys.argv[1]); "
            "h['guard'](sys.argv[2],sys.argv[3],json.load(open(sys.argv[4],encoding='utf-8')))")
    subprocess.run([str(python), "-I", "-B", "-c", code, str(payload / "scripts/install_identity.py"),
                    str(payload), str(runtime), str(candidate)], env=environment, check=True)
    subprocess.run([str(python), "-I", "-B", "-m", expected["module"], "check"], env=environment, check=True)
    if source_identity(payload) != expected:
        raise ValueError("Payload changed during provisioning")
    atomic_json(runtime / RECEIPT, receipt)
    print(json.dumps({"provisioned": True, "name": expected["name"], "version": expected["version"],
                      "code_sha256": expected["code_sha256"], "wheel_sha256": artifact["wheel_sha256"]}))
