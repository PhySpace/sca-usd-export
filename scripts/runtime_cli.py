"""Fixed entry executed by dispatch.ps1 with the add-on interpreter, -I and -B."""

import json
import sys


def main():
    # -I deliberately ignores PYTHONIOENCODING; set transport encoding here so
    # JSON/error paths containing Chinese survive the Windows pipe chain.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        print("Run this entry with python -I -B", file=sys.stderr)
        return 2
    try:
        from sca_usd_export.transport import decode_args

        if len(sys.argv) != 3 or sys.argv[1] != "argv-json-base64":
            raise ValueError("expected argv-json-base64 TOKEN")
        arguments = decode_args(sys.argv[2])
        from sca_usd_export.cli import main as cli_main

        return cli_main(arguments)
    except (ImportError, ValueError) as exc:
        print(f"sca-usd-export: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
