"""Exercise the real isolated Python entry point without building a CAD model."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sca_usd_export.transport import encode_args


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ENTRY = PROJECT_ROOT / "scripts" / "runtime_cli.py"


def _invoke(arguments, *, cwd, env=None):
    return subprocess.run(
        [sys.executable, "-I", "-B", str(RUNTIME_ENTRY), *arguments],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, check=False,
    )


def test_isolated_entry_ignores_hostile_cwd_and_pythonpath(tmp_path):
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
    result = _invoke(["argv-json-base64", encode_args(["--help"])], cwd=hostile, env=environment)
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
def test_runtime_entry_refuses_malformed_or_unencoded_arguments(tmp_path, arguments):
    result = _invoke(arguments, cwd=tmp_path)
    assert result.returncode != 0
    assert "traceback" not in result.stderr.lower()
    assert not list(tmp_path.iterdir())


def test_runtime_entry_preserves_chinese_error_path_as_utf8(tmp_path):
    missing = tmp_path / "不存在的模型 O'Brien & input.scadpkg"
    output = tmp_path / "未生成的输出.usda"
    result = _invoke(
        ["argv-json-base64", encode_args(["export", str(missing), str(output)])],
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert str(missing) in result.stderr
    assert "\ufffd" not in result.stderr
    assert "traceback" not in result.stderr.lower()
    assert not output.exists()
