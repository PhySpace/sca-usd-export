"""Exercise the real isolated Python entry point without building a CAD model."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import sysconfig
import tomllib
from types import SimpleNamespace
import venv

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHYSICS_ROOT = PROJECT_ROOT.parent / "sca-physics"


def encode_args(arguments):
    return base64.b64encode(json.dumps(arguments, ensure_ascii=False).encode("utf-8")).decode("ascii")


@pytest.fixture(scope="module")
def installed_entry(tmp_path_factory):
    """Real isolated venv plus byte-verified synthetic installation receipt.

    This is a CLI/identity test fixture, not a provision acceptance result.
    Third-party files are explicitly seeded from this test interpreter with
    hardlinks (copy fallback); no .pth/PYTHONPATH or system-site inheritance.
    Business packages are separate byte copies of the selected payloads.
    """
    root = tmp_path_factory.mktemp("runtime-boundary-install")
    runtime = root / "runtime"
    prefix = runtime / ".venv"
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(prefix)
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    child = subprocess.run([str(python), "-I", "-B", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                           capture_output=True, text=True, check=True)
    site = Path(child.stdout.strip())
    source_site = Path(sysconfig.get_path("purelib"))

    def filesystem_path(path):
        # Explicit Win32 extended paths keep third-party type stubs seedable
        # below a long pytest evidence directory; no OS setting is changed.
        text = os.path.abspath(path)
        if os.name == "nt" and not text.startswith("\\\\?\\"):
            return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text
        return text

    def link_or_copy(source, target):
        try:
            os.link(filesystem_path(source), filesystem_path(target))
        except OSError:
            shutil.copy2(filesystem_path(source), filesystem_path(target))
        return str(target)

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.pth")
    for item in source_site.iterdir():
        if item.name.startswith(("sca_physics", "sca_usd_export")) or item.name == "__pycache__" or item.suffix in (".pth", ".pyc", ".pyo"):
            continue
        if item.is_dir():
            # copytree also creates intermediate directories; prefixing only
            # its copy_function leaves deep third-party stub trees at MAX_PATH.
            shutil.copytree(filesystem_path(item), filesystem_path(site / item.name),
                            copy_function=link_or_copy, ignore=ignore)
        elif item.is_file():
            link_or_copy(item, site / item.name)
    payloads = {}
    for selected in (PROJECT_ROOT, PHYSICS_ROOT):
        info = tomllib.loads((selected / "pyproject.toml").read_text(encoding="utf-8-sig"))["project"]
        module = info["name"].replace("-", "_")
        payload = root / "payloads" / info["name"]
        (payload / "src").mkdir(parents=True)
        shutil.copytree(selected / "src" / module, payload / "src" / module, ignore=ignore)
        shutil.copyfile(selected / "pyproject.toml", payload / "pyproject.toml")
        shutil.copytree(payload / "src" / module, site / module)
        dist = site / (module + "-" + info["version"] + ".dist-info")
        dist.mkdir()
        (dist / "METADATA").write_text("Metadata-Version: 2.4\nName: " + info["name"] + "\nVersion: " + info["version"] + "\n", encoding="utf-8")
        payloads[info["name"]] = payload
    payload = payloads["sca-usd-export"]
    scripts = payload / "scripts"
    scripts.mkdir()
    for name in ("runtime_cli.py", "install_identity.py"):
        shutil.copyfile(PROJECT_ROOT / "scripts" / name, scripts / name)
    identity = runpy.run_path(str(scripts / "install_identity.py"))
    receipt = {"schema_version": 1, "status": "ready", "payload_root": str(payload.resolve()),
               "package": identity["source_identity"](payload),
               "peer": identity["source_identity"](payloads["sca-physics"])}
    identity["atomic_json"](runtime / identity["RECEIPT"], receipt)
    profile = root / "profile"
    profile.mkdir()
    return SimpleNamespace(root=root, runtime=runtime, python=python, site=site, profile=profile,
                           entry=scripts / "runtime_cli.py", payload=payload, receipt=runtime / identity["RECEIPT"])


def _invoke(installation, arguments, *, cwd, env=None, bind_runtime=True, python=None):
    environment = (os.environ if env is None else env).copy()
    environment.pop("PYTHONHOME", None)
    environment.update(USERPROFILE=str(installation.profile), HOME=str(installation.profile))
    if bind_runtime:
        environment["SCA_RUNTIME_DIR"] = str(installation.runtime)
    else:
        environment.pop("SCA_RUNTIME_DIR", None)
    return subprocess.run(
        [str(python or installation.python), "-I", "-B", str(installation.entry), *arguments],
        cwd=cwd, env=environment, capture_output=True, text=True, encoding="utf-8",
        timeout=30, check=False,
    )


def test_isolated_entry_ignores_hostile_cwd_and_pythonpath(tmp_path, installed_entry):
    hostile = tmp_path / "unsafe cwd with spaces 中文 & O'Brien"
    hostile.mkdir()
    package = hostile / "sca_usd_export"
    package.mkdir()
    sentinel = hostile / "unexpected-import.txt"
    (package / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('wrong package imported')\n"
        "raise RuntimeError('cwd/PYTHONPATH package was imported')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(hostile)
    # Deliberately omit inherited bytecode suppression. The entry command's
    # explicit -B must prevent caches independently of parent process settings.
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    result = _invoke(installed_entry, ["argv-json-base64", encode_args(["--help"])], cwd=hostile, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout.lower()
    assert not sentinel.exists()
    assert not list(hostile.rglob("__pycache__"))


@pytest.mark.parametrize("arguments", [
    [], ["argv-json-base64"], ["argv-json-base64", "!bad-base64!"],
    ["argv-json-base64", base64.b64encode(b'"--help"').decode("ascii")],
    ["export", "input.scadpkg", "output.usda"],
    ["argv-json-base64", base64.b64encode(b'["--help"]').decode("ascii"), "extra"],
])
def test_runtime_entry_refuses_malformed_or_unencoded_arguments(tmp_path, arguments, installed_entry):
    result = _invoke(installed_entry, arguments, cwd=tmp_path)
    assert result.returncode != 0
    assert "traceback" not in result.stderr.lower()
    assert not list(tmp_path.iterdir())


def test_runtime_entry_preserves_chinese_error_path_as_utf8(tmp_path, installed_entry):
    missing = tmp_path / "不存在的模型 O'Brien & input.scadpkg"
    output = tmp_path / "未生成的输出.usda"
    result = _invoke(
        installed_entry,
        ["argv-json-base64", encode_args(["export", str(missing), str(output)])],
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert str(missing) in result.stderr
    assert "\ufffd" not in result.stderr
    assert "traceback" not in result.stderr.lower()
    assert not output.exists()


def test_source_entry_is_rejected_without_runtime_binding(tmp_path, installed_entry):
    result = _invoke(installed_entry, ["argv-json-base64", encode_args(["--help"])], cwd=tmp_path, bind_runtime=False)
    assert result.returncode == 2
    assert "SCA_RUNTIME_DIR is missing" in result.stderr
    assert "traceback" not in result.stderr.lower()


def test_foreign_interpreter_is_rejected_before_business_import(tmp_path, installed_entry):
    result = _invoke(installed_entry, ["argv-json-base64", encode_args(["--help"])], cwd=tmp_path, python=sys.executable)
    assert result.returncode == 2
    assert "own .venv" in result.stderr
    assert "usage:" not in result.stdout.lower()


def test_same_version_stale_code_is_rejected_before_import(tmp_path, installed_entry):
    module = installed_entry.site / "sca_usd_export/__init__.py"
    before = module.read_bytes()
    try:
        module.write_bytes(before + b"\nraise RuntimeError('must reject stale bytes before import')\n")
        result = _invoke(installed_entry, ["argv-json-base64", encode_args(["--help"])], cwd=tmp_path)
        assert result.returncode == 2
        assert "runtime code differs" in result.stderr
        assert "traceback" not in result.stderr.lower()
    finally:
        module.write_bytes(before)


def test_missing_receipt_is_rejected_before_import(tmp_path, installed_entry):
    before = installed_entry.receipt.read_bytes()
    installed_entry.receipt.unlink()
    try:
        result = _invoke(installed_entry, ["argv-json-base64", encode_args(["--help"])], cwd=tmp_path)
        assert result.returncode == 2
        assert "receipt is missing" in result.stderr
    finally:
        installed_entry.receipt.write_bytes(before)
