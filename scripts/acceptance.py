"""Reproduce local SCA add-on installation in a disposable Windows profile.

Only --work-dir (a NEW directory) is written. Existing SCA configuration,
source packages and the host interpreter's dependencies are never changed.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sca-python", required=True, type=Path)
    parser.add_argument("--bootstrap-python", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--resume", action="store_true", help="resume this script's own failed work directory after an add-on fix")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    previous = []
    if args.resume:
        if not args.work_dir:
            raise ValueError("--resume requires --work-dir")
        work = args.work_dir.resolve()
        saved = json.loads((work / "acceptance.json").read_text(encoding="utf-8"))
        if saved["repository"] != str(repository):
            raise ValueError("work directory belongs to a different add-on")
        previous = saved["steps"]
    elif args.work_dir:
        work = args.work_dir.resolve()
        if work.is_relative_to(repository):
            raise ValueError("the acceptance directory must be outside the add-on payload")
        if any(char in str(work) for char in "'\"&|<>^%!()\r\n"):
            raise ValueError("choose an acceptance root without quotes or cmd metacharacters; spaces are supported")
        work.mkdir(parents=True, exist_ok=False)
    else:
        work = Path(tempfile.mkdtemp(prefix="sca usd addon acceptance ")).resolve()
    profile = work / "isolated profile"
    profile.mkdir(exist_ok=args.resume)
    caller = work / "unrelated working directory"
    caller.mkdir(exist_ok=args.resume)
    home, skills = work / "state with spaces" / "addons", work / "skills with spaces"
    installed = home / "sca-usd-export"
    runtime = home.parent / "runtimes" / "sca-usd-export"
    source = caller / "input models" / "CAD O'Brien & bracket.scadpkg"
    source.parent.mkdir(exist_ok=args.resume)
    if source.exists():
        if source.read_bytes() != args.package.resolve().read_bytes():
            raise ValueError("the resume input differs from the original package")
    else:
        shutil.copyfile(args.package.resolve(), source)
    output = caller / "output models" / "USD O'Brien & bracket.usda"
    env = dict(os.environ)
    env.update(USERPROFILE=str(profile), PYTHONDONTWRITEBYTECODE="1",
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               SCA_USD_BOOTSTRAP_PYTHON=str(args.bootstrap_python.resolve()),
               PIP_CACHE_DIR=str(runtime / "cache" / "pip"))
    env.pop("PYTHONPATH", None)
    env.pop("SCA_ADDON_HOME", None)
    env.pop("SCA_SKILLS_DIR", None)
    host = [str(args.sca_python.resolve()), "-I", "-B", "-m", "simplecadapi.cli"]
    management_python = args.sca_python.resolve()
    results = list(previous)
    overall_ok = False
    report_path = work / "acceptance.json"

    def record():
        report_path.write_text(json.dumps({
            "repository": str(repository), "work_dir": str(work),
            "addon_home": str(home), "skills_dir": str(skills),
            "runtime_dir": str(runtime), "output": str(output),
            "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "host_cli_python": str(args.sca_python.resolve()),
            "bootstrap_python": str(args.bootstrap_python.resolve()),
            "environment_overrides": {key: env[key] for key in (
                "USERPROFILE", "SCA_USD_BOOTSTRAP_PYTHON", "PYTHONDONTWRITEBYTECODE", "PIP_FIND_LINKS", "SCA_USD_WHEELHOUSE")
                if key in env},
            "steps": results, "github_install_tested": False,
            "isaac_sim_tested": False, "ok": overall_ok,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(label, command, expected=0):
        print(json.dumps({"step": label, "started": True, "work_dir": str(work)}), flush=True)
        log = work / (f"{len(results):02d}-" + label + ".log")
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.run(command, cwd=caller, env=env, stdout=stream,
                                     stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
        item = {"step": label, "command": command, "cwd": str(caller),
                "exit_code": process.returncode, "expected_exit_code": expected,
                "passed": process.returncode == expected, "log": str(log)}
        results.append(item)
        record()
        print(json.dumps(item), flush=True)
        if process.returncode != expected:
            raise RuntimeError(f"{label} failed; read {log}")
        return log

    # Confirm the profile redirect before the first configuration write.
    run("00-profile", [str(args.sca_python.resolve()), "-I", "-B", "-c",
        "from pathlib import Path; import os; assert Path.home().resolve() == Path(os.environ['USERPROFILE']).resolve(); print(Path.home())"])
    locations = ["--home", str(home), "--skills-dir", str(skills)]
    run("01-init", host + ["init", "--no-shell", *locations])
    if args.resume:
        run("02-update", host + ["addon", "update", *locations, "sca-usd-export"])
    else:
        run("02-install", host + ["addon", "add", *locations, str(repository)])
    run("03-provision", host + ["addon", "use", *locations, "sca-usd-export", "provision"])
    # After setup even the SCA manager can run from the add-on's own SDK wheel.
    management_python = runtime / ".venv" / "Scripts" / "python.exe"
    host = [str(management_python), "-I", "-B", "-m", "simplecadapi.cli"]
    run("04-check", host + ["addon", "check", *locations, "sca-usd-export"])
    registry = json.loads((home / "registry.json").read_text(encoding="utf-8"))
    state = registry["addons"]["sca-usd-export"]
    if not state.get("runtime_check", {}).get("passed"):
        # CLI check may return 0 even when the runtime probe failed.
        raise RuntimeError("SCA runtime probe did not pass; inspect the registry and check log")
    invocation = work / "invoke wrapper.ps1"
    invocation.write_text(
        "param([string]$Wrapper,[string]$ScaPython,[string]$AddonHome,[string]$SkillsDir,[string]$Request)\n"
        "$ErrorActionPreference='Stop'\n"
        "$values=[string[]](Get-Content -LiteralPath $Request -Raw -Encoding UTF8 | ConvertFrom-Json)\n"
        "& $Wrapper -ScaPython $ScaPython -AddonHome $AddonHome -SkillsDir $SkillsDir -Arguments $values\n"
        "exit $LASTEXITCODE\n", encoding="utf-8")

    def use(label, arguments, expected=0):
        request = work / (label + ".request.json")
        request.write_text(json.dumps(arguments, ensure_ascii=False), encoding="utf-8")
        return run(label, ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(invocation), "-Wrapper", str(installed / "scripts" / "use.ps1"),
            "-ScaPython", str(management_python), "-AddonHome", str(home),
            "-SkillsDir", str(skills), "-Request", str(request)], expected)

    facts_log = use("05-runtime-facts", ["check"])
    facts = json.loads(facts_log.read_text(encoding="utf-8"))
    if not Path(facts["executable"]).resolve().is_relative_to(runtime.resolve()):
        raise RuntimeError("the runtime borrowed the host interpreter")
    (work / "runtime-facts.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")
    use("06-export", ["export", str(source.relative_to(caller)), str(output.relative_to(caller))])
    use("07-validate", ["validate", str(output.relative_to(caller))])
    use("08-history", ["history", str(output.with_suffix('.history') / 'history.json')])
    use("09-preview", ["preview", str(output), str(output.with_suffix('.html'))])
    use("10-refuse-overwrite", ["export", str(source), str(output)], expected=2)
    use("11-missing-input", ["export", "missing package.scadpkg", str(caller / 'absent.usda')], expected=2)
    run("12-list", host + ["addon", "list", *locations])
    export_report = json.loads(output.with_suffix(".report.json").read_text(encoding="utf-8"))
    if not export_report["validation"]["ok"]:
        raise RuntimeError("USD validation did not pass")
    overall_ok = True
    record()
    print(json.dumps({"ok": True, "report": str(report_path), "output": str(output),
                      "runtime_facts": str(work / "runtime-facts.json")}), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"acceptance: {exc}", file=sys.stderr)
        raise SystemExit(2)
