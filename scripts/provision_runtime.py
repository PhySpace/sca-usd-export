"""Provision the selected USD/physics artifacts without borrowing host packages."""
import argparse
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", required=True, type=Path)
    args = parser.parse_args()
    scripts = Path(__file__).resolve().parent
    runpy.run_path(str(scripts / "install_identity.py"))["provision"](scripts.parent, args.runtime_dir)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f"sca-usd-export provision: {exc}", file=sys.stderr)
        raise SystemExit(2)
