"""A fast check of the installed add-on environment, separate from CAD modeling."""

import importlib.metadata
import importlib.util
import os
from pathlib import Path
import platform
import sys


def _under(path, directory):
    return Path(path).resolve().is_relative_to(Path(directory).resolve())


def runtime_facts(expected_runtime=None):
    prefix = Path(sys.prefix).resolve()
    if sys.prefix == sys.base_prefix:
        raise ValueError("the add-on must run in its own virtual environment")
    runtime = expected_runtime or os.environ.get("SCA_RUNTIME_DIR")
    if runtime and prefix != (Path(runtime) / ".venv").resolve():
        raise ValueError("interpreter does not belong to SCA_RUNTIME_DIR/.venv")
    if sys.platform != "win32" or platform.machine().lower() not in (
        "amd64", "x86_64"
    ):
        raise ValueError("this release is verified only on windows-x86_64")
    if sys.version_info[:2] != (3, 12):
        raise ValueError("this release requires CPython 3.12")
    venv_config = (prefix / "pyvenv.cfg").read_text(encoding="utf-8").lower()
    if "include-system-site-packages = false" not in venv_config:
        raise ValueError("system site-packages must not be inherited")
    distributions = {
        "simplecadapi": "simplecadapi",
        "usd-core": "pxr",
        "numpy": "numpy",
        "cadquery-ocp": "OCP",
        "plotly": "plotly",
        "sca-usd-export": "sca_usd_export",
    }
    if importlib.util.find_spec("sca_physics") is not None:
        from .dependencies import require_physics
        require_physics()
        distributions["sca-physics"] = "sca_physics"
    versions, origins = {}, {}
    for distribution, module in distributions.items():
        versions[distribution] = importlib.metadata.version(distribution)
        spec = importlib.util.find_spec(module)
        if spec is None or spec.origin is None or not _under(spec.origin, prefix):
            raise ValueError(f"{module} must be installed inside the add-on environment")
        origins[module] = str(Path(spec.origin).resolve())
    if versions["simplecadapi"] != "2.2.5":
        raise ValueError("this release requires the tested SimpleCADAPI 2.2.5")
    if versions["usd-core"] != "26.8":
        raise ValueError("this release requires usd-core 26.8")
    if versions["cadquery-ocp"] != "7.9.3.1":
        raise ValueError("this release requires the verified cadquery-ocp 7.9.3.1 kernel")
    # Load the native USD library and actually construct a stage. Full SCA/OCCT
    # imports and dependency consistency are checked during provisioning.
    from pxr import Usd, UsdPhysics

    stage = Usd.Stage.CreateInMemory()
    UsdPhysics.Scene.Define(stage, "/Probe")
    return {
        "ok": True,
        "executable": sys.executable,
        "prefix": str(prefix),
        "base_prefix": sys.base_prefix,
        "packages": versions,
        "package_origins": origins,
        "no_system_site_packages": True,
        "isolated_mode": bool(sys.flags.isolated),
        "bytecode_disabled": bool(sys.dont_write_bytecode),
        "physics_extension_available": "sca-physics" in versions,
    }
