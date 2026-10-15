"""Installed add-on CLI: python -m sca_usd_export."""

import argparse
import json
import sys

from .config import ExportConfig


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Independent SCA/.scadpkg to OpenUSD extension"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="check the independent add-on environment")
    export = commands.add_parser(
        "export", help="export a new USD and history/report bundle"
    )
    export.add_argument("input", help="input .scadpkg")
    export.add_argument("output", help="new .usda output; existing paths are refused")
    export.add_argument(
        "--config", help="JSON configuration overriding centralized defaults"
    )
    export.add_argument(
        "--up-axis",
        choices=("Y", "Z"),
        help="label CAD up axis; do not rotate coordinates",
    )
    validate = commands.add_parser(
        "validate", help="reopen USD and compare with its export report"
    )
    validate.add_argument("input", help=".usda with matching .report.json and .history")
    history = commands.add_parser("history", help="read genuine saved SCA operations")
    history.add_argument("manifest", help=".history/history.json")
    history.add_argument(
        "--definition", help="definition ID; print raw operation graph"
    )
    preview = commands.add_parser(
        "preview", help="make an offline interactive HTML preview"
    )
    preview.add_argument("input", help="USD file")
    preview.add_argument("output", help="new .html file")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            from .runtime import runtime_facts

            result = runtime_facts()
        elif args.command == "export":
            from dataclasses import replace
            from .exporter import export_package

            config = (
                ExportConfig.from_json(args.config) if args.config else ExportConfig()
            )
            if args.up_axis:
                config = replace(config, up_axis=args.up_axis)
            report = export_package(args.input, args.output, config=config)
            result = {
                key: report[key]
                for key in ("output", "history", "status", "validation")
            }
        elif args.command == "validate":
            from .validation import validate_usd

            result = validate_usd(args.input)
        elif args.command == "preview":
            from .preview import write_preview

            result = write_preview(args.input, args.output)
        else:
            from .history import read_feature_graph, read_history_bundle

            result = (
                dict(read_feature_graph(args.manifest, args.definition).graph)
                if args.definition
                else read_history_bundle(args.manifest)
            )
        # SCA 2.2.4 decodes runtime probes using the host locale. ASCII-escaped
        # probe JSON remains readable even when its install path is Unicode.
        print(json.dumps(result, ensure_ascii=args.command == "check", indent=2, allow_nan=False))
        return 0
    except Exception as exc:
        # The CLI boundary reports kernel/schema/IO failures without hiding them.
        print(f"usd_export: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
