"""Run the Windows wrapper against a native argv recorder, never the user state."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from simplecadapi.addon.shell import render_cmd_shim


PROJECT_ROOT = Path(__file__).resolve().parents[1]
USE = PROJECT_ROOT / "scripts/use.ps1"
HELPERS = PROJECT_ROOT / "scripts/native_process.ps1"


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _powershell(directory, script):
    powershell = shutil.which("powershell.exe")
    assert powershell, "the declared Windows runtime requires Windows PowerShell"
    entry = directory / "invoke.ps1"
    entry.write_text(script, encoding="utf-8-sig")
    profile = directory / "isolated-user"
    profile.mkdir(exist_ok=True)
    environment = dict(os.environ)
    environment.update(
        USERPROFILE=str(profile), HOME=str(profile), HOMEDRIVE=profile.drive,
        HOMEPATH=str(profile)[len(profile.drive):], APPDATA=str(profile),
        LOCALAPPDATA=str(profile), TEMP=str(profile), TMP=str(profile),
    )
    for key in ("SCA_ADDON_HOME", "SCA_SKILLS_DIR", "SCA_RUNTIME_DIR"):
        environment.pop(key, None)
    return subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(entry)],
        cwd=directory, env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=False,
    )


@pytest.fixture(scope="module")
def native_recorder(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native-argv-recorder")
    executable = directory / "sca.exe"
    # A real native process lets the test check ProcessStartInfo quoting and
    # Unicode argv. This recorder is not SCA and does not perform an installation.
    source = """
using System;
using System.Text;
public static class ArgvRecorder {
    public static int Main(string[] args) {
        foreach (string arg in args)
            Console.WriteLine(Convert.ToBase64String(Encoding.UTF8.GetBytes(arg)));
        return 0;
    }
}
"""
    result = _powershell(directory, "$ErrorActionPreference = 'Stop'\n"
                         f"Add-Type -OutputAssembly {_literal(executable)} "
                         f"-OutputType ConsoleApplication -TypeDefinition @'\n{source}\n'@\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert executable.is_file()
    return executable


def test_prepare_deformable_powershell_transport_preserves_special_paths(tmp_path):
    arguments = ["prepare-deformable", "中文 CAD/O'Brien & input.scadpkg",
                 "node/assembly/线缆", '输出/a "quoted" surface.json',
                 "--config", "配置 with spaces.json"]
    payload = tmp_path / "arguments.json"
    payload.write_text(json.dumps(arguments, ensure_ascii=False), encoding="utf-8")
    result = _powershell(tmp_path, "$ErrorActionPreference = 'Stop'\n"
                         f". {_literal(HELPERS)}\n"
                         f"$values = ConvertFrom-Json (Get-Content -Raw -Encoding UTF8 {_literal(payload)})\n"
                         "ConvertTo-ScaUsdEncodedArguments $values\n")
    assert result.returncode == 0, result.stdout + result.stderr
    decoded = json.loads(base64.b64decode(result.stdout.strip(), validate=True).decode("utf-8"))
    assert decoded == arguments


@pytest.mark.parametrize("command", ["check", "provision"])
@pytest.mark.parametrize("child_exit", [0, 2])
def test_dispatch_fixed_words_preserve_success_and_native_error_codes(tmp_path, command, child_exit):
    """Real dispatcher process; fake child isolates only its exit contract."""
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(PROJECT_ROOT / "scripts/dispatch.ps1", scripts / "dispatch.ps1")
    helper = HELPERS.read_text(encoding="utf-8")
    helper += """
function Start-ScaUsdNativeProcess {
    param($Executable, [string[]]$ArgumentList, [hashtable]$Environment)
    [Console]::WriteLine('child:' + $env:SCA_TEST_CHILD_EXIT)
    return [int]$env:SCA_TEST_CHILD_EXIT
}
"""
    (scripts / "native_process.ps1").write_text(helper, encoding="utf-8-sig")
    (scripts / "runtime_cli.py").write_text("# fake child entry; not executed\n")
    (scripts / "provision.ps1").write_text("# fake provision; not executed\n")
    runtime = tmp_path / "runtime"
    (runtime / ".venv/Scripts").mkdir(parents=True)
    (runtime / ".venv/Scripts/python.exe").write_bytes(b"fake child")
    result = _powershell(tmp_path, f"$env:SCA_TEST_CHILD_EXIT = '{child_exit}'\n"
                         f".({_literal(scripts / 'dispatch.ps1')}) -RuntimeDir {_literal(runtime)} {command}\n")
    assert result.returncode == child_exit, result.stdout + result.stderr
    assert f"child:{child_exit}" in result.stdout


@pytest.mark.parametrize("with_at", [True, False])
def test_wrapper_reads_sca_shim_and_forwards_encoded_paths(tmp_path, native_recorder, with_at):
    shim = tmp_path / "sca.cmd"
    text = render_cmd_shim(native_recorder)
    if not with_at:
        text = text.replace(f'@"{native_recorder}" %*', f'"{native_recorder}" %*')
    sentinel = tmp_path / "shim-was-executed.txt"
    # The wrapper must inspect the literal target rather than executing a .cmd.
    text += f'echo unexpected > "{sentinel}"\r\n'
    shim.write_text(text, encoding="utf-8")
    arguments = ["export", "中文/O'Brien & input.scadpkg", "输出 with spaces/result.usda"]
    array = "@(" + ",".join(_literal(arg) for arg in arguments) + ")"
    home, skills = tmp_path / "addon home", tmp_path / "skills 中文 & O'Brien"
    result = _powershell(tmp_path, f"& {_literal(USE)} -ScaCommand {_literal(shim)} "
                         f"-AddonHome {_literal(home)} -SkillsDir {_literal(skills)} -Arguments {array}\n"
                         "exit $LASTEXITCODE\n")
    assert result.returncode == 0, result.stdout + result.stderr
    forwarded = [base64.b64decode(line, validate=True).decode("utf-8") for line in result.stdout.splitlines()]
    assert forwarded[:-1] == ["addon", "use", "--home", str(home), "--skills-dir", str(skills),
                             "sca-usd-export", "argv-json-base64"]
    assert json.loads(base64.b64decode(forwarded[-1], validate=True).decode("utf-8")) == arguments
    assert not sentinel.exists()


def test_wrapper_default_sca_uses_first_path_match_with_multiple_candidates(tmp_path, native_recorder):
    first, second = tmp_path / "first bin", tmp_path / "second bin"
    first.mkdir()
    second.mkdir()
    selected = first / "sca.cmd"
    other = second / "sca.cmd"
    selected.write_text(render_cmd_shim(native_recorder), encoding="utf-8")
    # The later candidate is deliberately unusable. Default invocation must
    # select the first PATH command rather than merging all matching shims.
    other.write_text(render_cmd_shim(second / "missing/sca.exe"), encoding="utf-8")
    resolution = tmp_path / "command-resolution.json"
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$env:PATH = {_literal(str(first) + os.pathsep + str(second) + os.pathsep)} + $env:PATH\n"
        "$choices = @(Get-Command sca -CommandType Application -ErrorAction Stop)\n"
        f"$choices | Select-Object Source | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath {_literal(resolution)}\n"
        f"if ($choices.Count -lt 2 -or $choices[0].Source -ine {_literal(selected)}) {{ throw 'Test PATH did not select the expected first shim' }}\n"
        f"& {_literal(USE)} -Arguments @('prepare-deformable','--help')\n"
        "exit $LASTEXITCODE\n"
    )
    result = _powershell(tmp_path, script)
    candidates = json.loads(resolution.read_text(encoding="utf-8-sig"))
    assert candidates[0]["Source"] == str(selected)
    assert str(other) in [candidate["Source"] for candidate in candidates]
    assert result.returncode == 0, result.stdout + result.stderr
    forwarded = [base64.b64decode(line, validate=True).decode("utf-8") for line in result.stdout.splitlines()]
    assert forwarded[:-1] == ["addon", "use", "sca-usd-export", "argv-json-base64"]
    assert json.loads(base64.b64decode(forwarded[-1], validate=True).decode("utf-8")) == ["prepare-deformable", "--help"]


@pytest.mark.parametrize("invalid", ["duplicate", "wrong-name", "missing"])
def test_wrapper_keeps_strict_sca_shim_target_validation(tmp_path, native_recorder, invalid):
    if invalid == "duplicate":
        text = f'@"{native_recorder}" %*\r\n@"{native_recorder}" %*\r\n'
    elif invalid == "wrong-name":
        target = tmp_path / "other.exe"
        shutil.copyfile(native_recorder, target)
        text = f'@"{target}" %*\r\n'
    else:
        text = f'@"{tmp_path / "missing/sca.exe"}" %*\r\n'
    shim = tmp_path / "invalid.cmd"
    shim.write_text(text, encoding="utf-8")
    result = _powershell(tmp_path, f"& {_literal(USE)} -ScaCommand {_literal(shim)} -Arguments @('--help')\n"
                         "exit $LASTEXITCODE\n")
    assert result.returncode != 0
    assert "not a recognized SCA init shim" in result.stderr
