"""Bootstrap an independent environment; the host modeling environment is untouched."""

import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", required=True, type=Path)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12) or platform.machine().lower() not in (
        "amd64", "x86_64"
    ) or sys.platform != "win32":
        raise ValueError("provisioning requires CPython 3.12 on Windows x64")
    payload = Path(__file__).resolve().parents[1]
    runtime = args.runtime_dir.resolve()
    if not runtime.is_dir():
        raise ValueError("SCA must create the add-on runtime directory first")
    target = runtime / ".venv"
    python = target / "Scripts" / "python.exe"
    if not target.resolve().is_relative_to(runtime):
        raise ValueError("the virtual environment must remain inside the add-on runtime")
    if target.exists() and not python.is_file():
        raise ValueError("incomplete .venv exists; choose a fresh runtime, no files were deleted")
    if not target.exists():
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(target)
    configuration = (target / "pyvenv.cfg").read_text(encoding="utf-8").lower()
    if "include-system-site-packages = false" not in configuration:
        raise ValueError("refusing to provision an environment that inherits system packages")
    env = dict(os.environ)
    env.update(SCA_ADDON_DIR=str(payload), SCA_RUNTIME_DIR=str(runtime),
               PYTHONDONTWRITEBYTECODE="1", PIP_CACHE_DIR=str(runtime / "cache" / "pip"))
    def run(arguments):
        subprocess.run([str(python), "-I", "-B", *arguments], env=env, check=True)

    install_inputs = [str(payload)]
    # Optional pre-fetched wheels are still installed into this environment.
    # Explicit file arguments avoid pip choosing an identical remote candidate
    # over --find-links on slow connections. Pip resolves all version constraints.
    wheelhouse_value = os.environ.get("SCA_USD_WHEELHOUSE")
    if wheelhouse_value:
        wheelhouse = Path(wheelhouse_value).expanduser().resolve()
        wheels = sorted(wheelhouse.glob("*.whl")) if wheelhouse.is_dir() else []
        if not wheels:
            raise ValueError("SCA_USD_WHEELHOUSE must contain valid pre-fetched .whl files")
        install_inputs.extend(str(wheel) for wheel in wheels)
    run(["-m", "pip", "--disable-pip-version-check", "--timeout", "60", "--retries", "3",
         "install", "--upgrade", *install_inputs])
    run(["-m", "pip", "--disable-pip-version-check", "check"])
    run(["-c", "import simplecadapi, numpy, OCP; from pxr import Usd, UsdPhysics; import sca_usd_export; print('Native dependency imports passed')"])
    run(["-m", "sca_usd_export", "check"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"sca-usd-export provision: {exc}", file=sys.stderr)
        raise SystemExit(2)
