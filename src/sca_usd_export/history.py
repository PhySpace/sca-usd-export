"""Preserve SCA feature graphs and source snapshots beside a USD stage.

These assets describe and retain CAD history. Reading them does not execute
the modeling operations; geometric replay remains the responsibility of SCA
and its OCCT kernel. No SCA or OpenUSD dependency is imported at module load.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _filesystem_path(path: Path) -> Path:
    """Use Windows' native long-path form for file I/O, without changing URLs.

    CAD feature resources have content-addressed names. A perfectly ordinary
    result directory can therefore exceed MAX_PATH once the history suffix and
    digest are appended. No machine-wide long-path setting is required.
    """
    if os.name != "nt":
        return path
    absolute = str(path.absolute())
    if absolute.startswith("\\\\?\\"):
        return path
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _identity(definition: Any) -> dict[str, str]:
    return {
        key: str(getattr(definition, key))
        for key in ("definition_id", "definition_kind", "revision", "content_hash")
    }


def _definitions(root: Any) -> list[Any]:
    by_id: dict[str, Any] = {}
    active: set[str] = set()

    def visit(definition: Any) -> None:
        identity = _identity(definition)
        definition_id = identity["definition_id"]
        if definition_id in active:
            raise ValueError(f"Cyclic definition graph at {definition_id!r}")
        existing = by_id.get(definition_id)
        if existing is not None:
            if _identity(existing) != identity:
                raise ValueError(f"Conflicting definition identity: {definition_id!r}")
            return
        if identity["definition_kind"] not in {"single_solid", "assembly"}:
            raise ValueError(
                f"Unsupported SCA definition kind: {identity['definition_kind']}"
            )
        active.add(definition_id)
        by_id[definition_id] = definition
        for reference in getattr(definition, "definition_refs", ()):
            child = definition.resolved_definitions.get(reference.definition_id)
            if child is None:
                raise ValueError(
                    f"Unresolved SCA definition: {reference.definition_id!r}"
                )
            visit(child)
        active.remove(definition_id)

    visit(root)
    return [by_id[key] for key in sorted(by_id)]


def _write_resource(directory: Path, path: str, payload: bytes) -> dict[str, Any]:
    target = _filesystem_path(directory / path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(payload)
    return {"path": path, "sha256": _sha256(payload), "byte_length": len(payload)}


def write_history_bundle(
    root_definition: Any,
    output_dir: str | Path,
    *,
    package_content_hash: str | None = None,
) -> Path:
    """Write exact definition JSON and feature ZIPs to a new directory.

    Each original feature ZIP includes the original graph, dependency edges,
    operation parameters, semantic/topology provenance and captured Python
    source bytes. The manifest reports operations without source attribution.
    Existing directories are refused so previous exports cannot be overwritten.
    BREP, material and topology cache payloads remain in the original package;
    the definition JSON retains their original references.
    """
    from simplecadapi.artifacts.feature_graph import load_feature_graph_artifact

    destination = Path(output_dir).expanduser().resolve()
    if _filesystem_path(destination).exists():
        raise FileExistsError(f"History output directory already exists: {destination}")

    # Prepare and validate every payload before creating the output directory.
    prepared: list[tuple[Any, bytes, bytes, Any]] = []
    for definition in _definitions(root_definition):
        reference = definition.feature_graph_ref
        graph_bytes = bytes(definition.blobs[reference.path])
        if (
            _sha256(graph_bytes) != reference.sha256
            or len(graph_bytes) != reference.byte_length
        ):
            raise ValueError(
                f"Feature graph bytes differ for {definition.definition_id!r}"
            )
        artifact = load_feature_graph_artifact(graph_bytes)
        prepared.append(
            (definition, bytes(definition.canonical_bytes), graph_bytes, artifact)
        )

    _filesystem_path(destination).mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    for definition, definition_bytes, graph_bytes, artifact in prepared:
        identity = _identity(definition)
        stem = identity["content_hash"].removeprefix("sha256:")
        nodes = artifact.graph["nodes"]
        missing = [
            node["node_id"]
            for node in nodes
            if not node.get("source", {}).get("source_file_id")
        ]
        source_files = [source.to_dict() for source in artifact.source_files]
        record: dict[str, Any] = {
            **identity,
            "definition": _write_resource(
                destination, f"definitions/{stem}.json", definition_bytes
            ),
            "feature_graph": {
                **_write_resource(
                    destination, f"feature_graphs/{stem}.zip", graph_bytes
                ),
                "content_hash": artifact.content_hash,
            },
            "operation_count": len(nodes),
            "dependency_count": len(artifact.graph["edges"]),
            "result_node_ids": list(artifact.result_node_ids),
            "source_files": source_files,
            "operations_missing_source": missing,
        }
        records.append(record)

    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "artifact_kind": "simplecad_usd_history",
        "root": _identity(root_definition),
        "package_content_hash": package_content_hash,
        "definitions": records,
        "semantics": {
            "feature_graphs": "Unmodified SCA feature archives, including captured source files.",
            "definition_records": "Unmodified SCA definition JSON, including instances, relations and solved snapshots.",
            "geometry_cache": "BREP, material and topology payloads remain in the original .scadpkg; their references are preserved.",
            "source_missing": "Operations without a captured source-file ID are listed explicitly.",
            "execution": "History storage and reading do not rebuild geometry or execute operations in USD.",
            "node_identity": "SCA node IDs are definition-local; cross-revision stability requires explicit authoring policy.",
        },
    }
    manifest["content_hash"] = _sha256(_json_bytes(manifest))
    path = destination / "history.json"
    with _filesystem_path(path).open("xb") as stream:
        stream.write(_json_bytes(manifest))
    return path


def _resource_path(manifest_path: Path, record: dict[str, Any]) -> Path:
    relative = Path(record["path"])
    if relative.is_absolute() or ".." in relative.parts or "\\" in record["path"]:
        raise ValueError(f"Unsafe history resource path: {record['path']!r}")
    target = (manifest_path.parent / relative).resolve()
    if not target.is_relative_to(manifest_path.parent):
        raise ValueError(f"History resource escapes bundle: {record['path']!r}")
    return target


def read_history_bundle(manifest_path: str | Path) -> dict[str, Any]:
    """Read a bundle and verify all resource digests; no CAD execution occurs."""
    path = Path(manifest_path).expanduser().resolve()
    manifest = json.loads(_filesystem_path(path).read_bytes())
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "1.0"
        or manifest.get("artifact_kind") != "simplecad_usd_history"
    ):
        raise ValueError("Unsupported USD history bundle")
    unhashed = {key: value for key, value in manifest.items() if key != "content_hash"}
    if manifest.get("content_hash") != _sha256(_json_bytes(unhashed)):
        raise ValueError("USD history manifest digest differs")
    seen: set[str] = set()
    for definition in manifest["definitions"]:
        definition_id = definition["definition_id"]
        if definition_id in seen:
            raise ValueError(f"Duplicate history definition: {definition_id!r}")
        seen.add(definition_id)
        for role in ("definition", "feature_graph"):
            record = definition[role]
            payload = _filesystem_path(_resource_path(path, record)).read_bytes()
            if (
                len(payload) != record["byte_length"]
                or _sha256(payload) != record["sha256"]
            ):
                raise ValueError(
                    f"History {role} integrity check failed for {definition_id!r}"
                )
            if role == "definition":
                raw = json.loads(payload)
                if any(
                    raw[key] != definition[key]
                    for key in (
                        "definition_id",
                        "definition_kind",
                        "revision",
                        "content_hash",
                    )
                ):
                    raise ValueError(
                        f"History definition identity differs for {definition_id!r}"
                    )
    root = manifest["root"]
    if not any(
        all(record[key] == root[key] for key in root)
        for record in manifest["definitions"]
    ):
        raise ValueError("USD history root is missing from definitions")
    return manifest


def read_feature_graph(manifest_path: str | Path, definition_id: str) -> Any:
    """Return a validated SCA FeatureGraphArtifact for inspection or SCA replay.

    This function only loads history. To rebuild geometry, an application must
    explicitly call the SCA artifact's ``replay`` with matching child definitions.
    """
    from simplecadapi.artifacts.feature_graph import load_feature_graph_artifact

    path = Path(manifest_path).expanduser().resolve()
    manifest = read_history_bundle(path)
    for record in manifest["definitions"]:
        if record["definition_id"] == definition_id:
            artifact = load_feature_graph_artifact(
                _filesystem_path(_resource_path(path, record["feature_graph"]))
            )
            actual = (
                artifact.owner_definition_id,
                artifact.owner_definition_kind,
                artifact.owner_revision,
            )
            expected = (definition_id, record["definition_kind"], record["revision"])
            if (
                actual != expected
                or artifact.content_hash != record["feature_graph"]["content_hash"]
            ):
                raise ValueError(
                    f"Feature graph identity differs for {definition_id!r}"
                )
            return artifact
    raise KeyError(f"Definition {definition_id!r} is absent from the history bundle")
